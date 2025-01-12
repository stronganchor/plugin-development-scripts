import os
import json
import io
import requests
import zipfile
import tkinter as tk
from tkinter import filedialog, messagebox
import openai
import re

from openai import OpenAI

# --------------------------------------------------------------------
# Files that store last-used paths (so we can restore defaults)
LAST_COMBINE_PATH_FILE = 'last_combine_path.txt'
LAST_APPLY_PATH_FILE   = 'last_apply_path.txt'

# Directories to skip when creating combined code:
SKIP_DIRS = ["getid3", "iso-languages", "plugin-update-checker", "languages", "media"]

# Fetch the API key from environment variables
api_key = os.getenv("OPENAI_API_KEY")

if not api_key:
    print("Error: OPENAI_API_KEY environment variable is not set.")
    exit(1)

openai.api_key = api_key
client = OpenAI()

# Function to get available models with priority models at the top
"""
Retrieves the list of available OpenAI models, prioritizing specified models.
"""
def get_available_models():
    priority_models = ["o1-mini", "o1-preview", "gpt-4o", "gpt-4o-mini"]  # Define your priority models

    try:
        # Fetch the list of available models
        response = client.models.list()

        # Extract model IDs by iterating directly over the response
        fetched_models = [model.id for model in response]

        # Identify which priority models are available
        available_priority_models = [model for model in priority_models if model in fetched_models]

        # Exclude priority models from fetched_models to avoid duplication
        other_models = [model for model in fetched_models if model not in priority_models]

        # Combine: priority models first, then the rest
        models = available_priority_models + other_models

        return models

    except Exception as e:
        print(f"[ERROR] Could not fetch models: {e}")
        # Return priority models as fallback
        return priority_models

# ------------------------- NEW SYSTEM MESSAGE -------------------------
# This will instruct the model to produce valid JSON only.
SYSTEM_MESSAGE_FOR_JSON = {
    "role": "system",
    "content": (
        "You are a helpful assistant. You must respond with valid JSON only, "
        "and do not include any extra text. If refusing for safety reasons, respond with a valid JSON object "
        'containing `"refusal"`.'
    )
}
# ---------------------------------------------------------------------

def send_to_openai():
    raw_path = combine_path_var.get().strip()
    if not raw_path:
        messagebox.showerror("Error", "No repository URL or path provided.")
        return

    # Check if the input is a URL
    if raw_path.startswith("http://") or raw_path.startswith("https://"):
        # Download and extract the repository
        try:
            output_dir = os.path.join(os.getcwd(), "combined_output")
            extracted_dir = os.path.join(output_dir, "extracted")
            repo_path = download_github_repo(raw_path, extracted_dir)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to download repository: {e}")
            return
    elif os.path.isdir(raw_path):
        # Use the directory directly if it exists
        repo_path = raw_path
    else:
        messagebox.showerror("Error", f"Invalid path or URL: {raw_path}")
        return

    # Process the repository to combine code
    try:
        combined_code = process_repository(
            repo_path,
            output_dir=os.path.join(os.getcwd(), "combined_output"),
            skip_dirs=SKIP_DIRS,
            max_chars=512000,
            chars_per_token=4
        )
    except Exception as e:
        messagebox.showerror("Error", f"Failed to process repository: {e}")
        return

    # Get the user prompt
    user_prompt = user_prompt_var.get("1.0", tk.END).strip()
    if not user_prompt:
        messagebox.showwarning("Warning", "No prompt provided for OpenAI.")
        return

    # Prepare messages for OpenAI API - system message + user message
    messages = [
        SYSTEM_MESSAGE_FOR_JSON,  # <-- Always instruct model to output valid JSON
        {
            "role": "user",
            "content": f"{combined_code}\n\n{user_prompt_intro}\n\n{user_prompt}"
        }
    ]

    # Call OpenAI API
    try:
        selected_model = selected_model_var.get()
        response = client.chat.completions.create(
            messages=messages,
            model=selected_model,
            # ------------------ EXPLICITLY REQUEST JSON MODE ------------------
            response_format={"type": "json_object"},
            # ------------------------------------------------------------------
        )

        response_content = response.choices[0].message.content

        # In JSON mode, .content should already be valid JSON. We can attempt to load it:
        try:
            json_object = json.loads(response_content)
            formatted_json = json.dumps(json_object, indent=2)
            response_content = formatted_json
        except json.JSONDecodeError as e:
            messagebox.showerror("JSON Error", f"Failed to parse JSON from response:\n{e}")

        text_json.delete("1.0", tk.END)
        text_json.insert(tk.END, response_content)
        messagebox.showinfo("Success", "Response received from OpenAI!")
    except Exception as e:
        messagebox.showerror("Error", f"Failed to communicate with OpenAI: {e}")

# Downloads a ZIP file from the specified URL with retry logic.
"""
Downloads a GitHub repository as a ZIP file from the specified URL and extracts it.
Tries 'main' and 'master' branches by default.
Returns the path to the extracted repository.
"""
def download_github_repo(repo_url: str, extraction_dir: str, max_retries=3) -> str:
    if not repo_url.endswith('/'):
        repo_url += '/'

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

    if not os.path.exists(extraction_dir):
        os.makedirs(extraction_dir)

    with zipfile.ZipFile(io.BytesIO(zip_content)) as z:
        if not z.namelist():
            raise Exception("[ERROR] Zip archive is empty or invalid.")
        print("[DEBUG] Zip file contents:", z.namelist())
        z.extractall(extraction_dir)
        extracted_name = z.namelist()[0].split('/')[0]
        repo_path = os.path.join(extraction_dir, extracted_name)

    print(f"[DEBUG] Repository extracted to: {repo_path}")
    return repo_path

"""
Attempts to download a ZIP file from the given URL with retries.
Returns the raw bytes if successful, otherwise None.
"""
def fetch_zip(url, max_retries=3, timeout=30):
    for attempt in range(max_retries):
        try:
            print(f"[DEBUG] Attempt {attempt+1} - GET {url}")
            r = requests.get(url, timeout=timeout)
            print(f"[DEBUG] Status code: {r.status_code}")
            print(f"[DEBUG] Response size (bytes): {len(r.content)}")

            if r.status_code == 200:
                content_type = r.headers.get('content-type', '')
                print(f"[DEBUG] Content-Type: {content_type}")
                if 'zip' in content_type.lower() or r.content.startswith(b'PK\x03\x04'):
                    return r.content
                else:
                    print("[DEBUG] Response not recognized as a valid zip file. Retrying...\n")
            else:
                print(f"[DEBUG] Non-200 status code ({r.status_code}). Retrying...\n")
        except Exception as e:
            print(f"[DEBUG] Attempt {attempt+1} got exception: {e}. Retrying...\n")

    return None

def get_plugin_info(repo_path: str):
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
                if plugin_name or plugin_version:
                    break
            except Exception as e:
                print(f"[DEBUG] Could not read {fname} for plugin info: {e}")

    return plugin_name, plugin_version

"""
Processes the repository by combining code from various files into a single text file.
Excludes specified directories and adds custom AI instructions.
Returns the combined code as a string.
"""
def process_repository(repo_path: str,
                       output_dir: str,
                       skip_dirs: list,
                       max_chars: int,
                       chars_per_token: int,
                       plugin_name: str = None,
                       plugin_version: str = None):
    combined_contents = []
    included_files = []
    total_chars = 0

    ai_instructions = [
        "IMPORTANT CUSTOM INSTRUCTIONS FOR AI CHAT SESSION:\n",
        "You must respond **only** with a JSON array as specified below. **Do not include any other text or explanations**.\n",
        "When you propose code changes, output them as an array of JSON objects.\n",
        "Each object should have:\n",
        "  \"file\"         : The relative path of the file.\n",
        "  \"functionName\" : (Optional) The name of the function to affect.\n",
        "  \"lineCode\"     : (Optional) The exact code of the line to affect.\n",
        "  \"lineNumber\"   : (Optional) The line number near which the lineCode exists (used to disambiguate if multiple matches).\n",
        "  \"action\"       : One of [\"insert before\", \"insert after\", \"delete\", \"replace\"].\n",
        "  \"code\"         : (If inserting or replacing) Include the code to insert or replace.\n",
        "\n",
        "Each JSON object can target either an entire function or a single line within a function.\n",
        "If targeting a function, use \"functionName\". If targeting a specific line, use \"lineCode\".\n",
        "Also include \"lineNumber\" when targeting a specific line to help locate the correct line if multiple identical lines exist.\n",
        "You can perform actions such as inserting before/after, replacing, or deleting.\n",
        "\n",
        "Examples:\n",
        "Function-level change:\n",
        "[\n",
        "  {\n",
        "    \"file\": \"assets/js/quiz.js\",\n",
        "    \"functionName\": \"resetQuizState\",\n",
        "    \"action\": \"replace\",\n",
        "    \"code\": \"function resetQuizState() {\\n    // entire new code here\\n}\"\n",
        "  },\n",
        "  {\n",
        "    \"file\": \"assets/js/quiz.js\",\n",
        "    \"functionName\": \"randomHelper\",\n",
        "    \"action\": \"delete\"\n",
        "  }\n",
        "]\n",
        "\n",
        "Line-level change:\n",
        "[\n",
        "  {\n",
        "    \"file\": \"assets/js/quiz.js\",\n",
        "    \"lineCode\": \"var score = 0;\",\n",
        "    \"action\": \"replace\",\n",
        "    \"code\": \"var score = 10;\"\n",
        "  },\n",
        "  {\n",
        "    \"file\": \"assets/js/quiz.js\",\n",
        "    \"lineCode\": \"console.log('Quiz started');\",\n",
        "    \"action\": \"delete\"\n",
        "  },\n",
        "  {\n",
        "    \"file\": \"assets/js/quiz.js\",\n",
        "    \"lineCode\": \"var i = 0;\",\n",
        "    \"lineNumber\": 150,\n",
        "    \"action\": \"insert before\",\n",
        "    \"code\": \"console.log('Initializing counter');\"\n",
        "  }\n",
        "]\n",
        "\n",
        "NOTES:\n",
        "- To target a function, specify \"functionName\".\n",
        "- To target a specific line, specify \"lineCode\".\n",
        "- Optionally, include \"lineNumber\" to help locate the correct line if multiple identical lines exist.\n",
        "- You can perform actions such as \"insert before\", \"insert after\", \"delete\", or \"replace\".\n",
        "- When inserting before or after a line or function, provide the exact code you want to insert.\n",
        "- When replacing, provide the new code that should replace the target.\n",
        "- Keep changes minimal and do not modify unrelated code.\n",
        "\n",
        "END OF INSTRUCTIONS.\n\n"
    ]

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
    print(f"[DEBUG] Approx tokens: {approx_tokens}")

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

    base_name = plugin_name if plugin_name else "all_code"
    if plugin_version:
        base_name += f" v{plugin_version}"
    output_filename = os.path.join(output_dir, f"{base_name}.txt")

    final_output = intro_block + "".join(combined_contents) + "".join(ai_instructions)

    with open(output_filename, "w", encoding="utf-8") as outfile:
        outfile.write(final_output)

    total_chars_in_file = len(final_output)
    print(f"[DEBUG] Wrote {output_filename} with {total_chars_in_file} characters "
          f"(approx {total_chars_in_file // chars_per_token} tokens).")

    return final_output

def apply_function_level_change(lines, func_name, action, code, file_extension):
    if func_name:
        if file_extension == '.py':
            return apply_python_function_change(lines, func_name, action, code)
        elif file_extension in ['.php', '.js']:
            return apply_brace_delimited_function_change(lines, func_name, action, code)
        else:
            print(f"[WARNING] Unsupported file extension '{file_extension}'. No changes applied.")
            return lines
    else:
        return apply_line_level_change(lines, action, code, line_code=code)

def apply_line_level_change(lines, action, new_code, line_code=None, reference_line_number=None):
    if not line_code:
        print(f"[WARNING] Line code not provided for line-level change.")
        return lines

    matching_indices = [i for i, line in enumerate(lines) if line.strip() == line_code.strip()]
    if not matching_indices:
        print(f"[WARNING] No lines matching code '{line_code}' found. No changes applied.")
        return lines

    if len(matching_indices) > 1 and reference_line_number:
        reference_idx = reference_line_number - 1
        closest_idx = min(matching_indices, key=lambda x: abs(x - reference_idx))
    else:
        closest_idx = matching_indices[0]

    if action == "insert before":
        if new_code:
            new_code_lines = new_code.splitlines(True)
            lines = lines[:closest_idx] + new_code_lines + lines[closest_idx:]
    elif action == "insert after":
        if new_code:
            new_code_lines = new_code.splitlines(True)
            lines = lines[:closest_idx + 1] + new_code_lines + lines[closest_idx + 1:]
    elif action == "delete":
        del lines[closest_idx]
    elif action == "replace":
        if new_code:
            new_code_lines = new_code.splitlines(True)
            lines = lines[:closest_idx] + new_code_lines + lines[closest_idx + 1:]
    else:
        print(f"[WARNING] Unknown action '{action}' for line-level change. No changes applied.")

    return lines

def apply_python_function_change(lines, func_name, action, code):
    func_def_pattern = re.compile(r'^def\s+' + re.escape(func_name) + r'\s*\(')
    start_idx = None
    end_idx = None
    decorator_start = None

    for i, line in enumerate(lines):
        if func_def_pattern.match(line.strip()):
            start_idx = i
            j = i - 1
            while j >= 0 and lines[j].strip().startswith('@'):
                decorator_start = j
                j -= 1
            if decorator_start is not None:
                start_idx = decorator_start
            break

    if start_idx is None:
        print(f"[WARNING] Could not find function '{func_name}' in Python file. No changes applied.")
        return lines

    func_indent = len(lines[start_idx]) - len(lines[start_idx].lstrip())

    for j in range(start_idx + 1, len(lines)):
        stripped_line = lines[j].strip()
        if not stripped_line:
            continue
        current_indent = len(lines[j]) - len(lines[j].lstrip())
        if current_indent <= func_indent and not stripped_line.startswith('@'):
            end_idx = j - 1
            break
    else:
        end_idx = len(lines) - 1

    if end_idx is None:
        end_idx = len(lines) - 1

    if action == "insert before":
        if code:
            new_code_lines = code.splitlines(True)
            insertion_idx = decorator_start if decorator_start is not None else start_idx
            lines = lines[:insertion_idx] + new_code_lines + lines[insertion_idx:]
    elif action == "insert after":
        if code:
            new_code_lines = code.splitlines(True)
            lines = lines[:end_idx + 1] + new_code_lines + lines[end_idx + 1:]
    elif action == "delete":
        del lines[start_idx:end_idx + 1]
    elif action == "replace":
        if code:
            new_code_lines = code.splitlines(True)
            lines = lines[:start_idx] + new_code_lines + lines[end_idx + 1:]
    else:
        print(f"[WARNING] Unknown action '{action}' for function '{func_name}' in Python file. No changes applied.")

    return lines

def apply_brace_delimited_function_change(lines, func_name, action, code):
    pattern = f"function {func_name}("
    start_idx = None

    for i, line in enumerate(lines):
        if pattern in line:
            start_idx = i
            break

    if start_idx is None:
        print(f"[WARNING] Could not find function {func_name}. No changes applied.")
        return lines

    if action == "insert before":
        if code is not None:
            new_code_lines = code.splitlines(True)
            lines = lines[:start_idx] + new_code_lines + lines[start_idx:]
        else:
            print(f"[WARNING] 'insert before' but no code provided for {func_name}.")
        return lines

    brace_depth = 0
    func_end_idx = start_idx
    found_open_brace = False

    for j in range(start_idx, len(lines)):
        if '{' in lines[j]:
            brace_depth += lines[j].count('{')
            found_open_brace = True
        if '}' in lines[j] and found_open_brace:
            brace_depth -= lines[j].count('}')
        if found_open_brace and brace_depth == 0:
            func_end_idx = j
            break

    if action == "insert after":
        if code is not None:
            new_code_lines = code.splitlines(True)
            lines = lines[:func_end_idx+1] + new_code_lines + lines[func_end_idx+1:]
        else:
            print(f"[WARNING] 'insert after' but no code provided for {func_name}.")
        return lines

    if action == "delete":
        del lines[start_idx:func_end_idx+1]
        return lines

    if action == "replace":
        if code is None:
            print(f"[WARNING] 'replace' action but no code provided for {func_name}.")
            return lines
        del lines[start_idx:func_end_idx+1]
        new_code_lines = code.splitlines(True)
        lines = lines[:start_idx] + new_code_lines + lines[start_idx:]
        return lines

    print(f"[WARNING] Unknown action '{action}' for {func_name}. No changes applied.")
    return lines

def apply_all_changes(repo_path, json_content):
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
        required_keys = ["file", "action"]
        if not all(k in change for k in required_keys):
            print(f"[WARNING] Incomplete change object: {change}")
            continue

        file_rel = change["file"]
        func_name = change.get("functionName", None)
        line_code = change.get("lineCode", None)
        line_number = change.get("lineNumber", None)
        action = change["action"].lower()
        code = change.get("code", None)
        if code and not code.endswith('\n'):
            code = code + '\n'

        target_file = os.path.join(repo_path, file_rel)
        file_extension = os.path.splitext(file_rel)[1]

        if not os.path.exists(target_file):
            print(f"[WARNING] File does not exist: {target_file}")
            continue

        try:
            with open(target_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except Exception as e:
            print(f"[ERROR] Could not read file '{target_file}' - {e}")
            continue

        if func_name:
            updated_lines = apply_function_level_change(lines, func_name, action, code, file_extension)
        elif line_code:
            updated_lines = apply_line_level_change(lines, action, code, line_code=line_code, reference_line_number=line_number)
        else:
            print(f"[WARNING] Neither 'functionName' nor 'lineCode' provided for change: {change}")
            continue

        try:
            with open(target_file, 'w', encoding='utf-8') as f:
                f.writelines(updated_lines)
            print(f"[INFO] Changes applied to {file_rel}")
        except Exception as e:
            print(f"[ERROR] Could not write file '{target_file}' - {e}")

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

def browse_folder_for_combine():
    folder_selected = filedialog.askdirectory()
    if folder_selected:
        combine_path_var.set(folder_selected)

def browse_folder_for_apply():
    folder_selected = filedialog.askdirectory()
    if folder_selected:
        apply_path_var.set(folder_selected)

def do_download_and_combine():
    raw_input = combine_path_var.get().strip()
    if not raw_input:
        messagebox.showerror("Error", "No URL/path provided for combining code.")
        return

    save_last_path(LAST_COMBINE_PATH_FILE, raw_input)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, "combined_output")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    extracted_dir = os.path.join(output_dir, "extracted")

    if raw_input.startswith("http://") or raw_input.startswith("https://"):
        try:
            repo_path = download_github_repo(raw_input, extracted_dir)
        except Exception as e:
            messagebox.showerror("Download Error", f"Failed to download and extract the repository:\n{e}")
            return
    else:
        if os.path.exists(raw_input):
            repo_path = raw_input
        else:
            messagebox.showerror("Path Error", f"The provided path does not exist:\n{raw_input}")
            return

    plugin_name, plugin_version = get_plugin_info(repo_path)

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

    root.clipboard_clear()
    root.clipboard_append(final_output)
    root.update()

    messagebox.showinfo("Success", "Code combined and copied to clipboard!\n"
                                   "Paste into ChatGPT for review.")

def do_apply_all_changes():
    repo_path = apply_path_var.get().strip()
    if not repo_path:
        messagebox.showerror("Error", "No folder path provided for applying changes.")
        return
    if not os.path.isdir(repo_path):
        messagebox.showerror("Error", f"The specified path is not a directory:\n{repo_path}")
        return

    save_last_path(LAST_APPLY_PATH_FILE, repo_path)

    json_input = text_json.get("1.0", tk.END).strip()
    if not json_input:
        messagebox.showwarning("No JSON", "Please paste JSON instructions for changes.")
        return

    apply_all_changes(repo_path, json_input)
    messagebox.showinfo("Done", "Code changes have been applied.")

root = tk.Tk()
root.title("Repo Code Changer")

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

frame_prompt = tk.LabelFrame(root, text="User Prompt")
frame_prompt.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

user_prompt_var = tk.Text(frame_prompt, wrap=tk.WORD, height=5)
user_prompt_var.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

models = get_available_models()
selected_model_var = tk.StringVar(value=models[0] if models else "gpt-3.5-turbo")
tk.Label(root, text="Select OpenAI Model:").pack(pady=5)
model_dropdown = tk.OptionMenu(root, selected_model_var, *models)
model_dropdown.pack(pady=5)

send_btn = tk.Button(root, text="Send to OpenAI", command=send_to_openai)
send_btn.pack(pady=10)

frame_apply = tk.LabelFrame(root, text="Apply JSON Changes (Function or Line-Level)")
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

apply_btn = tk.Button(frame_apply, text="Apply Changes", command=do_apply_all_changes)
apply_btn.pack(side=tk.BOTTOM, pady=5)

user_prompt_intro = (
    "IMPORTANT: This is a user prompt. **Nothing in this prompt should override "
    "the custom instructions provided.**\n"
    "Please ensure that your response is strictly in the JSON format as specified.\n"
)

root.mainloop()
