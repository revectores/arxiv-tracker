import sys
import requests
import os
import json
import time
import re
import subprocess
import xattr
import plistlib
import csv

from pathlib import Path
from hjfy_config import cookies

METADATA_ROOT = Path('/Users/rex/Library/Mobile Documents/com~apple~CloudDocs/metadata')
TRANSLATE_LOG = METADATA_ROOT / 'translated_log.csv'

http_proxy = 'http://127.0.0.1:7890'
proxies = { 
    "http": http_proxy, 
    "https": http_proxy, 
    "ftp": http_proxy
}
proxies = {}

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3',
}

# 'https://www.arxiv.org/pdf/2504.04310' => '2504.04310'
def extract_arxiv_id_from_pdf_source_link(link):
    arxiv_id = link.split('/')[-1]
    if 'v' in arxiv_id:
        arxiv_id = arxiv_id.split('v')[0]
    print(arxiv_id)
    return arxiv_id

def get_hjfy_page_link(arxiv_id):
    hjfy_url = f'https://hjfy.top/arxiv/{arxiv_id}'
    return hjfy_url

def get_hjfy_file_links_data(arxiv_id):
    file_link_api = 'https://hjfy.top/api/arxivFiles/{arxiv_id}'
    r = requests.get(file_link_api.format(arxiv_id=arxiv_id), headers=headers, cookies=cookies, proxies=proxies)
    if r.status_code != 200:
            print(f"Error fetching file links: {r.status_code}")
            return
    resp = r.json()
    file_links_data = resp['data']
    return file_links_data

def get_hjfy_file_status(arxiv_id):
    file_status_api = 'https://hjfy.top/api/arxivStatus/{arxiv_id}'
    r = requests.get(file_status_api.format(arxiv_id=arxiv_id), headers=headers, cookies=cookies, proxies=proxies)
    if r.status_code != 200:
            print(f"Error fetching file status: {r.status_code}")
            return
    resp = r.json()
    file_status = resp['data']['status']
    return file_status

def download_file(url, path):
    try:
        response = requests.get(url, headers=headers, cookies=cookies, stream=True, proxies=proxies)
        if response.status_code == 200:
            with open(path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            print(f"File downloaded successfully: {path}")
        else:
            print(f"Failed to download file: {response.status_code}")
    except Exception as e:
        print(f"Error downloading file: {e}")

def make_papername_singleline(name):
    name = name.replace('\n', ' ')
    name = re.sub(r'\s+', ' ', name)
    return name

def make_valid_darwin_filename(name):
    # Replace invalid characters with underscores
    name = name.replace('\n', ' ').replace(' : ', ' - ').replace(': ', ' - ').replace(':', ' - ')
    # Replace multiple spaces with a single space
    name = re.sub(r'\s+', ' ', name)
    # Remove leading and trailing spaces
    name = name.strip()
    return name

def make_filename(papername):
    root = '/Users/rex/Library/Mobile Documents/com~apple~CloudDocs/reference/Reference Translations'
    filename = f"{root}/{make_valid_darwin_filename(papername)} CN.pdf"
    return filename

def get_pdf_page_number(pdf_path):
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(pdf_path)
        return len(reader.pages)
    except Exception as e:
        print(f"Error reading PDF: {e}")
        return None

def open_translated_file(arxiv_id, open_file=True):
    # Check if the arxiv id is already downloaded in the log file
    if os.path.exists(TRANSLATE_LOG):
        with open(TRANSLATE_LOG, 'r', newline='') as log_file:
            csv_reader = csv.reader(log_file)
            for row in csv_reader:
                if row and row[0] == arxiv_id:
                    print(f"Found existing translation for {arxiv_id}")
                    papername = row[1]
                    filename = make_filename(papername)
                    if os.path.exists(filename):
                        if open_file:
                            subprocess.run(['open', filename])
                        return filename
                    else:
                        print(f"Translation file not found at {filename}, will download again")
                        break
                
    max_attempts = 100
    attempts = 0

    while attempts < max_attempts:
        file_status = get_hjfy_file_status(arxiv_id)
        if file_status == 'finished':
            file_links_data = get_hjfy_file_links_data(arxiv_id)
            papername, cn_file_link = file_links_data['title'], file_links_data.get('zhCN')
            papername = make_papername_singleline(papername)
            filename = make_filename(papername)
            download_file(cn_file_link, filename)
            # add download arxiv id with its name into log csv file
            with open(TRANSLATE_LOG, 'a') as log_file:
                papername =    papername.replace('\n',' ')
                with open(TRANSLATE_LOG, 'a', newline='') as log_file:
                    csv_writer = csv.writer(log_file)
                    csv_writer.writerow([arxiv_id, papername])
            subprocess.run(['/usr/bin/SetFile', '-a', 'E', filename])
            if open_file:
                subprocess.run(['open', filename])
            return filename
        elif file_status == 'error':
            print(f"File {arxiv_id} status: {file_status}. Translation failed for arxiv ID: {arxiv_id}.")
            return None
        else:
            attempts += 1
            remaining = max_attempts - attempts
            print(f"File {arxiv_id} status: {file_status}. Waiting for translation to complete...")
            print(f"File {arxiv_id} is not ready yet. Attempt {attempts}/{max_attempts} - {remaining} remaining.")
            if attempts < max_attempts:
                time.sleep(30)
    print(f"File {arxiv_id} Max attempts ({max_attempts}) reached. File translation not completed.")
    return None

def translate_from_arxiv_id(arxiv_id, open_file=True):
    filename = open_translated_file(arxiv_id, open_file=open_file)
    return filename
