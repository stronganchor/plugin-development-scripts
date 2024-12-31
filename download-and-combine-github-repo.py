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

def get_plugin_info(repo_path: str):
    """
    Scans top-level .php files in the repo for Plugin Name and Version lines.
    Returns (plugin_name, plugin_version). If either is missing, it returns None for that.
    """
    plugin_name = None
    plugin_version = None

    top_level_files = [
        f for f in os.listdir(repo_path)
        if os.path.isfile(os.path.join(repo_path, f))
    ]

    for fname in top_level_files:
        if fname.lower().endswith(".php"):
            full_path = os.path.join(repo_path, fname)
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    contents = f.read()
                # Look for "Plugin Name:" and "Version:" lines
                for line in contents.splitlines():
                    if "Plugin Name:" in line:
                        # e.g. "Plugin Name: My Awesome Plugin"
                        name_part = line.split("Plugin Name:", 1)[1].strip()
                        name_part = name_part.strip("*/ ")
                        if name_part:
                            plugin_name = name_part
                            print(f"[DEBUG] Detected plugin name: {plugin_name}")

                    if "Version:" in line:
                        # e.g. "Version: 1.2.3"
                        version_part = line.split("Version:", 1)[1].strip()
                        version_part = version_part.strip("*/ ")
                        if version_part:
                            plugin_version = version_part
                            print(f"[DEBUG] Detected plugin version: {plugin_version}")

                # If found either Plugin Name or Version, we can stop scanning further files
                # at the top level. (One file is typically enough to detect plugin info.)
                if plugin_name or plugin_version:
                    break
            except Exception as e:
                print(f"[DEBUG] Could not read {fname} for plugin info: {e}")

    return plugin_name, plugin_version

def process_repository(repo_path: str, output_dir: str, skip_dirs: list, max_chars: int, chars_per_token: int, plugin_name: str = None, plugin_version: str = None):
    """
    Walks through repo_path, reads text files, and writes them to one .txt file in output_dir.
    Skips directories in skip_dirs. If plugin_name is found, that replaces 'all_code'.
    If plugin_version is found, that is appended (i.e. v{plugin_version}).
    The file is overwritten if it already exists.

    Also includes custom instructions at the very top of the output file for subsequent AI usage.
    """

    combined_contents = []
    included_files = []
    total_chars = 0

    # Custom instructions (added to the top of the final output)
    ai_instructions = [
        "IMPORTANT CUSTOM INSTRUCTIONS FOR AI CHAT SESSION:\n",
        "1) When making code changes, always include:\n",
        "   - The **entire updated code file** (if it is small enough), OR\n",
        "   - The **entire updated function**, if only updating one function in a large code file.\n",
        "2) Always specify which file and folder the changes belong to.\n",
        "3) Only add relevant code comments, and do NOT include comments that just describe\n",
        "   how or why you changed something (for example, \"// here is the updated code\"), or\n",
        "   references to the user's instructions. Comments are strictly for logic, clarity, and maintainability.\n",
        "4) Make code changes that are easy to review with a diff tool.\n"
        "   - Keep the existing file structure and code ordering intact. Avoid reordering or\n",
        "     removing functions that are unrelated to the requested changes.\n",
        "   - If you're adding new code (e.g. helper functions), you may insert it wherever it\n",
        "     makes sense for readability (e.g. near its primary caller or at the bottom of the file).\n",
        "   - Avoid large-scale rearrangements or reformattings that create unnecessary diff noise.\n",
        "   - Do not delete or modify comments that are unrelated to the code changes you are making.\n",
        "\n",
        "END OF INSTRUCTIONS.\n\n"
    ]

    # Gather all code
    for root, dirs, files in os.walk(repo_path, topdown=True):
        print(f"[DEBUG] Scanning '{root}' with {len(dirs)} subdirectories and {len(files)} files BEFORE skipping.")
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        print(f"[DEBUG] After skipping, scanning '{root}' with {len(dirs)} subdirectories.")

        for filename in files:
            filepath = os.path.join(root, filename)
            relative_path = os.path.relpath(filepath, repo_path)
            header = f"--- {relative_path} ---\n"

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

    approx_tokens = total_chars // chars_per_token
    print(f"[DEBUG] Total characters read: {total_chars}")
    print(f"[DEBUG] Approximate tokens: {approx_tokens}")

    # Build an introduction block
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

    # Decide on base filename
    base_name = plugin_name if plugin_name else "all_code"
    if plugin_version:
        base_name += f" v{plugin_version}"
    output_filename = os.path.join(output_dir, f"{base_name}.txt")

    # Combine everything:
    #  1) AI instructions
    #  2) Introduction block
    #  3) All code content
    final_output = "".join(ai_instructions) + intro_block + "".join(combined_contents)

    # Write a single output file (overwrite if it exists)
    with open(output_filename, "w", encoding="utf-8") as outfile:
        outfile.write(final_output)

    total_chars_in_file = len(final_output)
    print(f"[DEBUG] Wrote {output_filename} with {total_chars_in_file} characters "
          f"(approx {total_chars_in_file // chars_per_token} tokens).")

def main():
    # Determine the script directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    url_file = os.path.join(script_dir, 'last_url.txt')  # File to store the last URL

    # Read the last URL if it exists
    if os.path.exists(url_file):
        with open(url_file, 'r') as f:
            last_url = f.read().strip()
        if last_url:
            prompt = f"Enter the repository location (GitHub URL or file path) [default: {last_url}]: "
        else:
            last_url = ''
            prompt = "Enter the repository location (GitHub URL or file path): "
    else:
        last_url = ''
        prompt = "Enter the repository location (GitHub URL or file path): "

    # Prompt the user for input, showing the default if available
    repo_input = input(prompt).strip()

    if not repo_input and last_url:
        repo_input = last_url
        print(f"Using the default URL: {repo_input}")
    elif repo_input:
        # Save the new URL to the file
        with open(url_file, 'w') as f:
            f.write(repo_input)
    else:
        print("No repository URL provided and no default URL set.")
        return  # Exit the script gracefully

    # If the repo input is a URL, download and extract to a subdir; otherwise, use the local path
    repo_name = repo_input.rstrip('/').split('/')[-1]
    output_dir = os.path.join(script_dir, repo_name)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Subdirectory for the extracted repo
    extracted_dir = os.path.join(output_dir, "extracted")

    # Determine if input is a URL or local path
    if repo_input.startswith("http://") or repo_input.startswith("https://"):
        try:
            repo_path = download_github_repo(repo_input, extracted_dir)
        except Exception as e:
            print(f"Failed to download and extract the repository: {e}")
            return  # Exit the script gracefully
    else:
        if os.path.exists(repo_input):
            repo_path = repo_input  # local path
        else:
            print(f"The provided local path does not exist: {repo_input}")
            return  # Exit the script gracefully

    # Check for WP plugin name & version
    plugin_name, plugin_version = get_plugin_info(repo_path)

    # Set up token/char limits
    max_tokens = 128000
    chars_per_token = 4
    max_chars = max_tokens * chars_per_token

    # Directories to skip
    skip_dirs = ["getid3", "iso-languages", "plugin-update-checker", "languages", "media"]

    process_repository(
        repo_path,
        output_dir,
        skip_dirs,
        max_chars,
        chars_per_token,
        plugin_name=plugin_name,
        plugin_version=plugin_version
    )
    print("Done.")

if __name__ == "__main__":
    main()
