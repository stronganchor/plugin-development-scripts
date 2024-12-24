import os
import requests
import zipfile
import io
import tempfile

def fetch_zip(url, max_retries=3, timeout=30):
    """
    Attempt to GET a zip file from the specified URL, up to max_retries times.
    Returns the raw bytes of the zip content if successful, or None on failure.
    """
    for attempt in range(max_retries):
        try:
            print(f"[DEBUG] Attempt {attempt+1} - GET {url}")
            r = requests.get(url, timeout=timeout)
            print(f"[DEBUG] Status code: {r.status_code}")
            print(f"[DEBUG] Response size (bytes): {len(r.content)}")

            if r.status_code == 200:
                content_type = r.headers.get('content-type', '')
                print(f"[DEBUG] Content-Type: {content_type}")

                # Check if the response *looks* like a zip
                # Basic check: "zip" in the content-type OR the first few bytes match PK header
                if 'zip' in content_type.lower() or r.content.startswith(b'PK\x03\x04'):
                    return r.content
                else:
                    print("[DEBUG] Response not recognized as a valid zip file. Retrying...\n")
            else:
                print("[DEBUG] Non-200 status code. Retrying...\n")

        except Exception as e:
            print(f"[DEBUG] Attempt {attempt+1} got exception: {e}. Retrying...\n")

    # If we exit the loop, all attempts failed
    return None

import os
import io
import zipfile

def download_github_repo(repo_url: str, temp_dir: str, max_retries=3) -> str:
    """
    Downloads the GitHub repo zip from either 'main' or 'master' branch.
    Extracts it into temp_dir and returns the extracted repo path.
    """
    if not repo_url.endswith('/'):
        repo_url += '/'

    # You can expand this if your repo uses a different default branch
    branches_to_try = ["main", "master"]
    zip_content = None

    for branch in branches_to_try:
        zip_url = repo_url + f"archive/refs/heads/{branch}.zip"
        print(f"[DEBUG] Trying branch '{branch}': {zip_url}")
        zip_content = fetch_zip(zip_url, max_retries=max_retries)
        if zip_content:
            # If we got valid zip bytes, stop searching
            break

    if not zip_content:
        raise Exception(f"[ERROR] Failed to download repository from {repo_url} "
                        f"(tried branches {branches_to_try}).")

    # Extract the zip into the temp_dir
    with zipfile.ZipFile(io.BytesIO(zip_content)) as z:
        if not z.namelist():
            raise Exception("[ERROR] Zip archive is empty or invalid.")

        print("[DEBUG] Zip file contents:", z.namelist())
        z.extractall(temp_dir)

        # Typically the first top-level folder is something like 'repo-main/'
        extracted_name = z.namelist()[0].split('/')[0]
        repo_path = os.path.join(temp_dir, extracted_name)

    print(f"[DEBUG] Repository extracted to: {repo_path}")
    return repo_path

def process_repository(repo_path: str, output_dir: str, skip_dirs: list, max_chars: int, chars_per_token: int):
    """
    Walks through repo_path, reads text files, and writes them to split .txt files in output_dir.
    Skips directories in skip_dirs. Prints debug info about each file read.
    """

    combined_contents = []
    total_chars = 0
    included_files = []

    for root, dirs, files in os.walk(repo_path, topdown=True):
        # Debug: see what directories/files are found before skipping
        print(f"[DEBUG] Scanning '{root}' with {len(dirs)} subdirectories and {len(files)} files BEFORE skipping.")

        # Skip directories
        dirs[:] = [d for d in dirs if d not in skip_dirs]

        # Debug: see what directories remain after skipping
        print(f"[DEBUG] After skipping, scanning '{root}' with {len(dirs)} subdirectories.")

        for filename in files:
            filepath = os.path.join(root, filename)
            relative_path = os.path.relpath(filepath, repo_path)
            header = f"--- {relative_path} ---\n"

            # Try reading file content
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                print(f"[DEBUG] Read {len(content)} characters from '{relative_path}'")
            except Exception as e:
                print(f"[DEBUG] Could not read file '{relative_path}' - {e}")
                content = "<Could not read file>"

            file_text = header + content + "\n\n"
            combined_contents.append(file_text)
            included_files.append(relative_path)
            total_chars += len(file_text)

    # Approximate tokens
    approx_tokens = total_chars // chars_per_token
    print(f"[DEBUG] Total characters read across all files: {total_chars}")
    print(f"[DEBUG] Approximate tokens: {approx_tokens}")

    # Create introduction block
    included_files.sort()
    intro_lines = [
        "This is the code from the provided repository.\n\n",
        "Note: The following folders were excluded from the code extraction:\n"
    ]
    for sd in skip_dirs:
        intro_lines.append(f"- {sd}\n")
    
    intro_lines.append("\nBelow is the file/folder structure of all **included** files:\n\n")
    for fpath in included_files:
        intro_lines.append(f"{fpath}\n")
    intro_lines.append("\n\n")

    intro_block = "".join(intro_lines)

    # Prepend the introduction block to combined contents
    combined_contents.insert(0, intro_block)
    total_chars += len(intro_block)

    # Split into multiple text files if needed
    file_count = 1
    current_chars = 0
    current_batch = []

    for file_text in combined_contents:
        if current_chars + len(file_text) > max_chars and current_chars > 0:
            # Write out the current batch
            output_filename = os.path.join(output_dir, f"all_code_{file_count}.txt")
            with open(output_filename, "w", encoding="utf-8") as outfile:
                outfile.write("".join(current_batch))
            print(f"[DEBUG] Wrote {output_filename} with {current_chars} characters "
                  f"(approx {current_chars // chars_per_token} tokens).")

            file_count += 1
            current_batch = [file_text]
            current_chars = len(file_text)
        else:
            current_batch.append(file_text)
            current_chars += len(file_text)

    # Write any remaining batch
    if current_batch:
        output_filename = os.path.join(output_dir, f"all_code_{file_count}.txt")
        with open(output_filename, "w", encoding="utf-8") as outfile:
            outfile.write("".join(current_batch))
        print(f"[DEBUG] Wrote {output_filename} with {current_chars} characters "
              f"(approx {current_chars // chars_per_token} tokens).")

def main():
    repo_input = input("Enter the repository location (GitHub URL or file path): ").strip()
    
    if repo_input.startswith("http://") or repo_input.startswith("https://"):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = download_github_repo(repo_input, tmpdir)
            repo_name = repo_input.rstrip('/').split('/')[-1]
    else:
        repo_path = repo_input
        if not os.path.isdir(repo_path):
            print("Invalid file path. Please make sure the directory exists and try again.")
            return
        repo_name = os.path.basename(repo_path)

    max_tokens = 128000  # 128k tokens max per file
    chars_per_token = 4
    max_chars = max_tokens * chars_per_token

    # Directories to skip
    skip_dirs = ["getid3", "iso-languages", "plugin-update-checker", "languages"]

    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, repo_name)

    # Create output directory if not exists
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    process_repository(repo_path, output_dir, skip_dirs, max_chars, chars_per_token)

    print("Done.")

if __name__ == "__main__":
    main()
