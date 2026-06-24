import requests,tarfile,os,re,tempfile,json
import shutil
from bs4 import BeautifulSoup
import fitz
import xml.etree.ElementTree as ET

def convert_pdf_figures_to_png(figure_paths, root_dir, article_id, output_base="build/figures", dpi=200):
    article_id = article_id.replace('.','_')

    output_dir = os.path.join(output_base, article_id)
    if os.path.exists(output_dir):
        return {'figures':[]}
    
    os.makedirs(output_dir)

    updated_paths = []

    for fig in figure_paths:
        src_path = os.path.join(root_dir, fig)

        if not os.path.isfile(src_path):
            continue

        ext = os.path.splitext(fig)[-1].lower()

        if ext == ".pdf":
            doc = fitz.open(src_path)
            page = doc.load_page(0)

            pix = page.get_pixmap(dpi=dpi)

            filename = os.path.splitext(os.path.basename(fig))[0] + ".png"
            dst_path = os.path.join(output_dir, filename)

            pix.save(dst_path)
            doc.close()

            updated_paths.append(dst_path.replace('build/','').replace("\\", "/"))
        elif ext == ".png" or ext == ".jpg":
            filename = os.path.basename(fig)
            dst_path = os.path.join(output_dir, filename)
            shutil.copy2(src_path, dst_path)

            updated_paths.append(dst_path.replace('build/','').replace("\\", "/"))

    return {'figures':updated_paths}

def download_file(url, dest_path):
    response = requests.get(url)
    response.raise_for_status()
    with open(dest_path, 'wb') as f:
        f.write(response.content)

def extract_archive(archive_path, extract_to):
    try:
        with tarfile.open(archive_path, 'r:*') as tar:
            tar.extractall(extract_to, filter='data')
        return True
    except tarfile.ReadError:
        return False

def find_first_tex_file(directory):
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(".tex"):
                return os.path.join(root, file)
    return None

def extract_keywords(tex_file_path):
    with open(tex_file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    match = re.search(r'\\keywords\s*{([^}]*)}', content, re.DOTALL)
    if match:
        return {'keywords':match.group(1).strip()}
    return {'keywords':''}

def extract_figure_paths(tex_file_path, root_dir, max_figures=10):
    with open(tex_file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    figure_paths = []

    # --- Extract raw references from LaTeX ---
    graphics_matches = re.findall(
        r'\\includegraphics(?:\[[^\]]*\])?\s*{([^}]*)}',
        content
    )

    raw_matches = re.findall(
        r'([^\s{}]+\.(?:png|jpg|jpeg|pdf|eps))',
        content,
        re.IGNORECASE
    )

    candidates = graphics_matches + raw_matches

    tex_dir = os.path.dirname(tex_file_path)
    seen = set()

    for ref in candidates:
        ref = ref.strip()

        # Possible extensions if missing
        possible_exts = ["", ".png", ".jpg", ".jpeg", ".pdf", ".eps"]

        found_path = None

        for ext in possible_exts:
            candidate_path = ref if ref.lower().endswith(ext) else ref + ext

            # 1. Try relative to tex file
            abs_path = os.path.normpath(os.path.join(tex_dir, candidate_path))
            if os.path.isfile(abs_path):
                found_path = abs_path
                break

            # 2. Try relative to root archive
            abs_path = os.path.normpath(os.path.join(root_dir, candidate_path))
            if os.path.isfile(abs_path):
                found_path = abs_path
                break

        # 3. Fallback: search entire archive by filename
        if not found_path:
            filename = os.path.basename(ref)
            for root, _, files in os.walk(root_dir):
                for f in files:
                    if f.startswith(filename):
                        found_path = os.path.join(root, f)
                        break
                if found_path:
                    break

        if found_path:
            rel_path = os.path.relpath(found_path, root_dir)
            if rel_path not in seen:
                seen.add(rel_path)
                figure_paths.append(rel_path)

        if len(figure_paths) >= max_figures:
            break

    return figure_paths[:max_figures]

def extract_arxiv_metadata(url):
    response = requests.get(url)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # --- Extract title ---
    title_tag = soup.find("h1", class_="title mathjax")
    title = None
    if title_tag:
        # arXiv titles usually look like "Title: Actual title"
        title = title_tag.get_text(strip=True)
        if title.lower().startswith("title:"):
            title = title[len("title:"):].strip()

    # --- Extract authors ---
    authors = []
    authors_div = soup.find("div", class_="authors")
    if authors_div:
        for a in authors_div.find_all("a"):
            authors.append(a.get_text(strip=True))

    return {
        "title": title,
        "authors": authors
    }

def get_new_arxiv_links(arxiv_cat):
    rss_url = "https://rss.arxiv.org/rss/astro-ph." + arxiv_cat
    print(rss_url)
    response = requests.get(rss_url)
    response.raise_for_status()

    # Parse XML
    root = ET.fromstring(response.content)

    # Namespace map (important!)
    namespaces = {
        "arxiv": "http://arxiv.org/schemas/atom"
    }

    new_links = []

    # Iterate over all <item> elements
    for item in root.findall(".//item"):

        announce_type = item.find("arxiv:announce_type", namespaces)

        if announce_type is not None and (announce_type.text == "new" or announce_type.text == "cross"):
            link = item.find("link")
            if link is not None and link.text:
                new_links.append(link.text.strip().split('/')[-1])

    return new_links
    
def extract_html(id,html_url):
    abs_url = 'https://arxiv.org/abs/{}'.format(id)

    response = requests.get(html_url)
    html_soup = BeautifulSoup(response.text, "html.parser")
    response = requests.get(abs_url)
    abs_soup = BeautifulSoup(response.text, "html.parser")

    article = html_soup.find('article',class_='ltx_document')
    
    figures = []

    for i,figure in enumerate(article.find_all("figure")):
        for img in figure.find_all('img'):
            src = img['src']
            if '/' in src and id in src:
                src = '/'.join(src.split('/')[1:])
            if '.' not in src:
                continue
            fig_url = html_url + '/' + src
            if requests.get(fig_url).status_code == 200:
                figures.append(fig_url)

    if len(figures) == 0:
        return None
    if len(figures) > 10:
        figures = figures[:10]
    
    authors = []
    for author in abs_soup.find('div',class_='authors').find_all('a'):
        authors.append(author.text)

    title = abs_soup.find('h1',class_='title').text.replace('Title:','')

    keywords = ''
    keyword_tag = html_soup.find('div',class_='ltx_keywords')
    
    if keyword_tag:
        kw = keyword_tag.find('span',id='id2.id1')
        if kw:
            keywords = kw.text

    return {'title':title,'authors':authors,'keywords':keywords,'figures':figures}
    
def get_html_url(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.text, "html.parser")

    link = soup.find('a',id='latexml-download-link')
    if link:
        return link['href']
    return None

def check_has_html(url):
    response = requests.get(url)
    if response.status_code != 200:
        return False
    return True

def main(id):
    tex_url = 'https://arxiv.org/src/{}'.format(id)
    abs_url = 'https://arxiv.org/abs/{}'.format(id)
    html_url = get_html_url(abs_url)

    if html_url is not None:
        print(id,'Extracting html metadata')
        return extract_html(id,html_url)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        filename = tex_url.split("/")[-1]
        archive_path = os.path.join(tmpdir, filename)

        print(id,"Downloading archive...")
        download_file(tex_url, archive_path)

        result = extract_archive(archive_path, tmpdir)
        if result is False:
            return None

        tex_file = None
        readme_path = os.path.join(tmpdir, "00README.json")
        if os.path.isfile(readme_path):
            with open(readme_path) as f:
                data = json.load(f)
                for source in data['sources']:
                    if source['usage'] == 'toplevel':
                        tex_file = os.path.join(tmpdir,source['filename'])
                        break
        
        if tex_file is None:
            print("Searching for .tex file...")
            tex_file = find_first_tex_file(tmpdir)

        if not tex_file:
            print("No .tex file found.")
            return None

        keywords = extract_keywords(tex_file)

        figure_paths = extract_figure_paths(tex_file,tmpdir)
        if len(figure_paths) == 0:
            print('No figures, skipping')
            return None

        figures = convert_pdf_figures_to_png(
            figure_paths,
            root_dir=tmpdir,
            article_id=id
        )
        if len(figures['figures']) == 0:
            return None

        metadata = extract_arxiv_metadata(abs_url)

        return metadata | keywords | figures

arxiv_cats = ['GA','EP','CO','HE','IM','SR']

if os.path.exists('build/figures'):
    shutil.rmtree('build/figures')

for arxiv_cat in arxiv_cats:
    global_metadata = {}

    ids = get_new_arxiv_links(arxiv_cat)

    for id in ids:
        res = main(id)
        if res is not None:
            global_metadata[id] = res

    json_file = 'build/articles_astro-ph.{}.json'.format(arxiv_cat)

    with open(json_file, 'w') as fp:
        json.dump(global_metadata, fp, indent=4)

shutil.copyfile('src/index.html','build/index.html')
