import os
import requests
import zipfile
import io

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

                # Basic check if it's likely a zip
                if 'zip' in content_type.lower() or r.content.startswith(b'PK\x03\x04'):
                    return r.content
                else:
                    print("[DEBUG] Response not recognized as a valid zip file. Retrying...\n")
            else:
                print(f"[DEBUG] Non-200 status code ({r.status_code}). Retrying...\n")

        except Exception as e:
            print(f"[DEBUG] Attempt {attempt+1} got exception: {e}. Retrying...\n")

    return None  # All attempts failed


def download_github_repo(repo_url: str, extraction_dir: str, max_retries=3) -> str:
    """
    Downloads the GitHub repo zip (from 'main' or 'master') into extraction_dir and returns the path.
    Leaves the extracted files in place for inspection.
    """
    if not repo_url.endswith('/'):
        repo_url += '/'

    # Try 'main' first, then 'master'
    branches_to_try = ["main", "master"]
    zip_content = None

    for branch in branches_to_try:
        zip_url = repo_url + f"archive/refs/heads/{branch}.zip"
        print(f"[DEBUG] Trying branch '{branch}': {zip_url}")
        zip_content = fetch_zip(zip_url, max_retries=max_retries)
        if zip_content:
            # If we got valid zip bytes, stop looking
            break

    if not zip_content:
        raise Exception(f"[ERROR] Failed to download repository from {repo_url} "
                        f"(tried branches {branches_to_try}).")

    # Ensure the extraction directory exists
    if not os.path.exists(extraction_dir):
        os.makedirs(extraction_dir)

    # Extract the zip to extraction_dir
    with zipfile.ZipFile(io.BytesIO(zip_content)) as z:
        if not z.namelist():
            raise Exception("[ERROR] Zip archive is empty or invalid.")

        print("[DEBUG] Zip file contents:", z.namelist())
        z.extractall(extraction_dir)

        # Typically the first folder is something like 'repo-main/' or 'repo-master/'
        extracted_name = z.namelist()[0].split('/')[0]
        repo_path = os.path.join(extraction_dir, extracted_name)

    print(f"[DEBUG] Repository extracted to: {repo_path}")
    return repo_path


def process_repository(repo_path: str, output_dir: str, skip_dirs: list, max_chars: int, chars_per_token: int):
    """
    Walks through repo_path, reads text files, and writes them to .txt files in output_dir.
    Skips directories in skip_dirs. Includes debug logs.
    """

    combined_contents = []
    total_chars = 0
    included_files = []

    for root, dirs, files in os.walk(repo_path, topdown=True):
        print(f"[DEBUG] Scanning '{root}' with {len(dirs)} subdirectories and {len(files)} files BEFORE skipping.")
        dirs[:] = [d for d in dirs if d not in skip_dirs]  # Skip certain dirs
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

    # Introduction block
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

    # Prepend the introduction block
    combined_contents.insert(0, intro_block)
    total_chars += len(intro_block)

    # Split into multiple .txt files if needed
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

    # We’ll assume you always want to make subdirectories in the same place as this script.
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # If the repo input is a URL, we’ll download it into a subdir named after the repo + '/extracted'
    # If it's a local path, we just use that local path.
    repo_name = repo_input.rstrip('/').split('/')[-1]
    output_dir = os.path.join(script_dir, repo_name)

    # Create the output directory if it doesn’t exist
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Subdirectory for the extracted repo
    extracted_dir = os.path.join(output_dir, "extracted")

    if repo_input.startswith("http://") or repo_input.startswith("https://"):
        # Download + extract directly into extracted_dir
        repo_path = download_github_repo(repo_input, extracted_dir)
    else:
        # If local, no need to download; just assume it's the path we want to process
        # But if you want to physically copy it, you'd do so. Otherwise, just pass it directly.
        repo_path = repo_input

    # Adjust these as needed
    max_tokens = 128000
    chars_per_token = 4
    max_chars = max_tokens * chars_per_token

    # Directories to skip
    skip_dirs = ["getid3", "iso-languages", "plugin-update-checker", "languages"]

    process_repository(repo_path, output_dir, skip_dirs, max_chars, chars_per_token)

    print("Done.")


if __name__ == "__main__":
    main()
