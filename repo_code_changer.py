import os
import json
import io
import requests
import zipfile
import tkinter as tk
from tkinter import filedialog, messagebox

# --------------------------------------------------------------------
# Files that store last-used paths (so we can restore defaults)
LAST_COMBINE_PATH_FILE = 'last_combine_path.txt'
LAST_APPLY_PATH_FILE   = 'last_apply_path.txt'

# Directories to skip when creating combined code:
SKIP_DIRS = ["getid3", "iso-languages", "plugin-update-checker", "languages", "media"]

# --------------------------------------------------------------------
# Helper functions from your "latest version" script

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
                        name_part = line.split("Plugin Name:", 1)[1].strip()
                        name_part = name_part.strip("*/ ")
                        if name_part:
                            plugin_name = name_part
                            print(f"[DEBUG] Detected plugin name: {plugin_name}")

                    if "Version:" in line:
                        version_part = line.split("Version:", 1)[1].strip()
                        version_part = version_part.strip("*/ ")
                        if version_part:
                            plugin_version = version_part
                            print(f"[DEBUG] Detected plugin version: {plugin_version}")

                # If found either plugin name or version, stop scanning further
                if plugin_name or plugin_version:
                    break
            except Exception as e:
                print(f"[DEBUG] Could not read {fname} for plugin info: {e}")

    return plugin_name, plugin_version

# --------------------------------------------------------------------
# This function creates the combined code text (no line numbers) + AI instructions.
def process_repository(repo_path: str,
                       output_dir: str,
                       skip_dirs: list,
                       max_chars: int,
                       chars_per_token: int,
                       plugin_name: str = None,
                       plugin_version: str = None):
    """
    Walks through repo_path, reads text files, and writes them to one .txt file in output_dir.
    Skips directories in skip_dirs. If plugin_name is found, that replaces 'all_code'.
    If plugin_version is found, that is appended (e.g. "v{plugin_version}").
    The file is overwritten if it already exists.

    1) We do NOT add line numbers.
    2) The top of the output file instructs AI to provide entire function replacements in JSON form:
       [ 
         {
           "file": "relative/path/to/file.js",
           "functionName": "myFunction",
           "updatedCode": "function myFunction() {...}"
         }
       ]
    """

    combined_contents = []
    included_files = []
    total_chars = 0

    # Custom AI instructions (added to the top of the final output)
    ai_instructions = [
        "IMPORTANT CUSTOM INSTRUCTIONS FOR AI CHAT SESSION:\n",
        "1) When proposing code changes, output them as **an array of JSON objects**.\n",
        "   Each object in the array should contain:\n",
        "       \"file\"         : The relative path of the file.\n",
        "       \"functionName\" : The name of the function being replaced.\n",
        "       \"updatedCode\"  : The **entire** updated code for that function.\n",
        "\n",
        "   Example:\n",
        "   [\n",
        "     {\n",
        "       \"file\": \"assets/js/quiz.js\",\n",
        "       \"functionName\": \"resetQuizState\",\n",
        "       \"updatedCode\": \"function resetQuizState() {\\n    ... entire new code ... \\n}\"\n",
        "     }\n",
        "   ]\n",
        "\n",
        "2) Provide updated code for entire functions, rather than partial line-based edits.\n",
        "3) Keep changes minimal and do not remove or modify unrelated code.\n",
        "4) Do not rename or remove existing functions unless explicitly asked.\n",
        "\n",
        "END OF INSTRUCTIONS.\n\n"
    ]

    # Gather code from all files
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

            # No line numbering: just raw content
            file_text = header + content + "\n\n"
            combined_contents.append(file_text)
            included_files.append(relative_path)
            total_chars += len(file_text)

    approx_tokens = total_chars // chars_per_token
    print(f"[DEBUG] Total characters read: {total_chars}")
    print(f"[DEBUG] Approx tokens: {approx_tokens}")

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

    # Decide on the output file name
    base_name = plugin_name if plugin_name else "all_code"
    if plugin_version:
        base_name += f" v{plugin_version}"
    output_filename = os.path.join(output_dir, f"{base_name}.txt")

    # Combine instructions + intro + all code
    final_output = "".join(ai_instructions) + intro_block + "".join(combined_contents)

    # Write out
    with open(output_filename, "w", encoding="utf-8") as outfile:
        outfile.write(final_output)

    total_chars_in_file = len(final_output)
    print(f"[DEBUG] Wrote {output_filename} with {total_chars_in_file} characters "
          f"(approx {total_chars_in_file // chars_per_token} tokens).")

    return final_output  # Return the text so we can copy to clipboard

# --------------------------------------------------------------------
# The "apply changes" function (still line-based for now)
def apply_changes(repo_path, json_content):
    """
    Parse the JSON content (array of changes) and apply them to files in repo_path.
    Currently uses line-based logic:
      file, line, action, content
    """
    try:
        changes = json.loads(json_content)
    except json.JSONDecodeError as e:
        messagebox.showerror("JSON Error", f"Failed to parse JSON:\n{e}")
        return

    if not isinstance(changes, list):
        messagebox.showerror("Invalid JSON", "JSON root must be an array of changes.")
        return

    for change in changes:
        if not isinstance(change, dict):
            continue
        required_keys = ["file", "line", "action"]
        if not all(k in change for k in required_keys):
            continue

        file_rel = change["file"]
        line_num = change["line"]  # expected int
        action = change["action"].lower()
        content = change.get("content", None)

        target_file = os.path.join(repo_path, file_rel)
        if not os.path.exists(target_file):
            print(f"[WARNING] File does not exist: {target_file}")
            continue

        # Read file lines
        try:
            with open(target_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except Exception as e:
            print(f"[ERROR] Could not read file '{target_file}' - {e}")
            continue

        # Apply changes
        if action == "delete":
            if 1 <= line_num <= len(lines):
                lines.pop(line_num - 1)
            else:
                print(f"[WARNING] Invalid line number {line_num} for delete in {file_rel}.")
        elif action == "replace":
            if content is None:
                print(f"[WARNING] 'replace' action without 'content' in {file_rel}.")
            elif 1 <= line_num <= len(lines):
                lines[line_num - 1] = content + "\n"
            else:
                print(f"[WARNING] Invalid line number {line_num} for replace in {file_rel}.")
        elif action == "insert before":
            if content is None:
                print(f"[WARNING] 'insert before' action without 'content' in {file_rel}.")
            else:
                index = max(0, line_num - 1)
                lines.insert(index, content + "\n")
        elif action == "insert after":
            if content is None:
                print(f"[WARNING] 'insert after' action without 'content' in {file_rel}.")
            else:
                index = min(line_num, len(lines))
                lines.insert(index, content + "\n")
        else:
            print(f"[WARNING] Unknown action '{action}' in {file_rel}. Skipping.")

        # Write back
        try:
            with open(target_file, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            print(f"[INFO] Changes applied to {file_rel}")
        except Exception as e:
            print(f"[ERROR] Could not write file '{target_file}' - {e}")

# --------------------------------------------------------------------
# Utility for saving/loading last paths
def load_last_path(filename):
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except:
            pass
    return ""

def save_last_path(filename, path):
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(path.strip())
    except:
        pass

# --------------------------------------------------------------------
# TKINTER UI

def browse_folder_for_combine():
    folder_selected = filedialog.askdirectory()
    if folder_selected:
        combine_path_var.set(folder_selected)

def browse_folder_for_apply():
    folder_selected = filedialog.askdirectory()
    if folder_selected:
        apply_path_var.set(folder_selected)

def do_download_and_combine():
    """
    Called when user clicks 'Download & Combine':
    1. Determine if user-specified string is a URL or local path.
    2. If URL, download + extract. If path, use directly.
    3. Gather code, produce a single text file, copy the text to clipboard.
    """
    raw_input = combine_path_var.get().strip()
    if not raw_input:
        messagebox.showerror("Error", "No URL/path provided for combining code.")
        return

    # Save the path for future sessions
    save_last_path(LAST_COMBINE_PATH_FILE, raw_input)

    script_dir = os.path.dirname(os.path.abspath(__file__))

    # We'll create an 'output' folder specifically for the combined code
    output_dir = os.path.join(script_dir, "combined_output")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Decide local extraction path
    extracted_dir = os.path.join(output_dir, "extracted")

    # If it looks like an HTTP/HTTPS URL, try to download
    if raw_input.startswith("http://") or raw_input.startswith("https://"):
        try:
            # Optionally remove or reuse extracted_dir as needed
            repo_path = download_github_repo(raw_input, extracted_dir)
        except Exception as e:
            messagebox.showerror("Download Error", f"Failed to download and extract the repository:\n{e}")
            return
    else:
        # It's presumably a local path
        if os.path.exists(raw_input):
            repo_path = raw_input
        else:
            messagebox.showerror("Path Error", f"The provided path does not exist:\n{raw_input}")
            return

    # Detect plugin name/version
    plugin_name, plugin_version = get_plugin_info(repo_path)

    # Produce combined code
    max_tokens = 128000
    chars_per_token = 4
    max_chars = max_tokens * chars_per_token

    final_output = process_repository(
        repo_path,
        output_dir,
        SKIP_DIRS,
        max_chars,
        chars_per_token,
        plugin_name=plugin_name,
        plugin_version=plugin_version
    )

    # Copy to clipboard
    root.clipboard_clear()
    root.clipboard_append(final_output)
    root.update()  # let the clipboard actions take effect

    messagebox.showinfo("Success", "Code combined and copied to clipboard!\n"
                                   "Paste into ChatGPT for review.")

def do_apply_changes():
    """
    Called when user clicks 'Apply Changes'.
    1. Get the folder path for applying changes.
    2. Parse JSON from text field.
    3. Apply changes to that folder.
    """
    repo_path = apply_path_var.get().strip()
    if not repo_path:
        messagebox.showerror("Error", "No folder path provided for applying changes.")
        return
    if not os.path.isdir(repo_path):
        messagebox.showerror("Error", f"The specified path is not a directory:\n{repo_path}")
        return

    # Save the path for future sessions
    save_last_path(LAST_APPLY_PATH_FILE, repo_path)

    json_input = text_json.get("1.0", tk.END).strip()
    if not json_input:
        messagebox.showwarning("No JSON", "Please paste JSON instructions for changes.")
        return

    apply_changes(repo_path, json_input)
    messagebox.showinfo("Done", "Code changes have been applied.")

# --------------------------------------------------------------------
# Main UI Setup
root = tk.Tk()
root.title("Combined Code Tool")

# Frame for "Download & Combine"
frame_combine = tk.LabelFrame(root, text="Download & Combine Code")
frame_combine.pack(fill=tk.X, padx=10, pady=5)

combine_path_var = tk.StringVar(value=load_last_path(LAST_COMBINE_PATH_FILE))

tk.Label(frame_combine, text="Repo URL/Path:").pack(side=tk.LEFT, padx=5)
combine_entry = tk.Entry(frame_combine, textvariable=combine_path_var, width=50)
combine_entry.pack(side=tk.LEFT, padx=5)
browse_btn1 = tk.Button(frame_combine, text="Browse...", command=browse_folder_for_combine)
browse_btn1.pack(side=tk.LEFT, padx=5)
combine_btn = tk.Button(frame_combine, text="Download & Combine", command=do_download_and_combine)
combine_btn.pack(side=tk.LEFT, padx=5)

# Frame for "Apply Changes"
frame_apply = tk.LabelFrame(root, text="Apply JSON Changes")
frame_apply.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

apply_path_var = tk.StringVar(value=load_last_path(LAST_APPLY_PATH_FILE))

path_frame = tk.Frame(frame_apply)
path_frame.pack(fill=tk.X, pady=5)
tk.Label(path_frame, text="Folder Path:").pack(side=tk.LEFT, padx=5)
apply_entry = tk.Entry(path_frame, textvariable=apply_path_var, width=50)
apply_entry.pack(side=tk.LEFT, padx=5)
browse_btn2 = tk.Button(path_frame, text="Browse...", command=browse_folder_for_apply)
browse_btn2.pack(side=tk.LEFT, padx=5)

tk.Label(frame_apply, text="Paste JSON changes here:").pack(anchor=tk.W, padx=5)

text_json = tk.Text(frame_apply, wrap=tk.NONE, width=80, height=10)
text_json.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

apply_btn = tk.Button(frame_apply, text="Apply Changes", command=do_apply_changes)
apply_btn.pack(side=tk.BOTTOM, pady=5)

root.mainloop()
