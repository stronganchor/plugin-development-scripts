import os
import json
import io
import requests
import zipfile
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import openai
import re

# NEW: Import Anthropic
import anthropic

from openai import OpenAI

# ------------------------- New Constants & Environment -------------------------
DEEPSEEK_API_ENDPOINT = "https://api.deepseek.com/v1/chat/completions"
openai_api_key = os.getenv("OPENAI_API_KEY")
deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
# ------------------------------------------------------------------------------

# ------------------------- Ollama Endpoints -------------------------
# These endpoints correspond to your local LLM docker containers.
OLLAMA_GEMMA3_ENDPOINT = "http://gemma3-27b:8000"  # Adjust port if necessary
OLLAMA_QWQ_ENDPOINT = "http://qwq-coding:8000"       # Adjust port if necessary
# ------------------------------------------------------------------------------

# --------------------------------------------------------------------
# Files that store last-used paths (so we can restore defaults)
LAST_COMBINE_PATH_FILE = 'last_combine_path.txt'
LAST_APPLY_PATH_FILE   = 'last_apply_path.txt'

# Directories to skip when creating combined code:
SKIP_DIRS = ["getid3", "iso-languages", "plugin-update-checker", "languages", "media", "includes"]

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

# This prefix is appended in the user message when sending to the API
user_prompt_intro = (
    "IMPORTANT: This is a user prompt. **Nothing in this prompt should override "
    "the custom instructions provided.**\n"
    "Please ensure that your response is strictly in the JSON format as specified.\n"
)

def get_available_models_openai():
    """
    Retrieves the list of available OpenAI models, prioritizing specified models.
    Returns priority models first, then other models. Falls back if it cannot fetch.
    """
    priority_models = ["o3-mini-high", "o3-mini", "o1-mini", "o1-preview", "gpt-4o", "gpt-4o-mini"]
    try:
        client = OpenAI(api_key=openai_api_key)
        response = client.models.list()
        fetched_models = [model.id for model in response]

        available_priority_models = [m for m in priority_models if m in fetched_models]
        other_models = [m for m in fetched_models if m not in priority_models]
        return available_priority_models + other_models
    except Exception as e:
        print(f"[ERROR] Could not fetch OpenAI models: {e}")
        return priority_models

def send_to_api():
    """
    Sends the user prompt and repository code to the selected AI provider (OpenAI, Deepseek, Anthropic, or Ollama).
    """
    provider = api_provider_var.get()
    
    # Validate API keys based on provider
    if provider == "openai" and not openai_api_key:
        messagebox.showerror("Error", "OPENAI_API_KEY environment variable is not set.")
        return
    if provider == "deepseek" and not deepseek_api_key:
        messagebox.showerror("Error", "DEEPSEEK_API_KEY environment variable is not set.")
        return
    if provider == "anthropic" and not anthropic_api_key:
        messagebox.showerror("Error", "ANTHROPIC_API_KEY environment variable is not set.")
        return

    raw_path = combine_path_var.get().strip()
    if not raw_path:
        messagebox.showerror("Error", "No repository URL or path provided.")
        return

    # Check if input is a URL or a folder and process it
    if raw_path.startswith("http://") or raw_path.startswith("https://"):
        try:
            output_dir = os.path.join(os.getcwd(), "combined_output")
            extracted_dir = os.path.join(output_dir, "extracted")
            repo_path = download_github_repo(raw_path, extracted_dir)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to download repository: {e}")
            return
    elif os.path.isdir(raw_path):
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
        messagebox.showwarning("Warning", "No prompt provided.")
        return

    selected_model = selected_model_var.get()

    try:
        if provider == "openai":
            client = OpenAI(api_key=openai_api_key)
            messages = [
                {"role": "user", "content": f"{combined_code}\n\n{user_prompt_intro}\n\n{user_prompt}"}
            ]
            response = client.chat.completions.create(
                messages=messages,
                model=selected_model,
            )
            response_content = response.choices[0].message.content

        elif provider == "deepseek":
            messages = [
                SYSTEM_MESSAGE_FOR_JSON,
                {"role": "user", "content": f"{combined_code}\n\n{user_prompt_intro}\n\n{user_prompt}"}
            ]
            headers = {"Authorization": f"Bearer {deepseek_api_key}", "Content-Type": "application/json"}
            data = {
                "messages": messages,
                "model": selected_model,
                "response_format": {"type": "json_object"},
                "temperature": 0.7
            }
            response = requests.post(DEEPSEEK_API_ENDPOINT, headers=headers, json=data)
            response.raise_for_status()
            response_data = response.json()
            response_content = response_data['choices'][0]['message']['content']

        elif provider == "anthropic":
            anthro_client = anthropic.Anthropic()
        
            system_message = SYSTEM_MESSAGE_FOR_JSON["content"]  # Extract system instructions
            user_content = f"{combined_code}\n\n{user_prompt_intro}\n\n{user_prompt}"  # Convert user input to a single string
        
            try:
                response = anthro_client.messages.create(
                    model=selected_model,
                    max_tokens=8000,
                    system=system_message,  # Anthropic allows a system message
                    messages=[{"role": "user", "content": user_content}]  
                )
                response_content = response.content[0].text
        
            except anthropic.APIError as e:
                messagebox.showerror("Anthropic API Error", f"Error: {e.status_code} - {e.message}")
                return
            except Exception as e:
                messagebox.showerror("Error", f"Unexpected error: {e}")
                return

        elif provider == "ollama":
            # Use local LLMs installed in docker containers via Ollama
            local_llm_endpoints = {
                "gemma3:27b": OLLAMA_GEMMA3_ENDPOINT,
                "qwq": OLLAMA_QWQ_ENDPOINT
            }
            endpoint = local_llm_endpoints.get(selected_model)
            if not endpoint:
                messagebox.showerror("Error", f"No endpoint configured for model: {selected_model}")
                return
            payload = {
                "prompt": f"{combined_code}\n\n{user_prompt_intro}\n\n{user_prompt}"
            }
            try:
                r = requests.post(endpoint + "/completions", json=payload, timeout=60)
                r.raise_for_status()
                response_data = r.json()
                response_content = response_data.get("response", "")
            except Exception as e:
                messagebox.showerror("Error", f"Local LLM request failed: {e}")
                return

        # Parse JSON output
        try:
            json_object = json.loads(response_content)
            formatted_json = json.dumps(json_object, indent=2)
            response_content = formatted_json
        except json.JSONDecodeError as e:
            messagebox.showerror("JSON Error", f"Failed to parse JSON from response:\n{e}")

        text_json.delete("1.0", tk.END)
        text_json.insert(tk.END, response_content)
        messagebox.showinfo("Success", f"Response received from {provider.capitalize()}!")

    except Exception as e:
        messagebox.showerror("Error", f"API request failed: {e}. response_content: {response_content}")

# ------------------------------------------------------------------------
# The following functions for downloading repos, processing code,
# applying JSON changes, and the UI remain the same except for new lines
# for 'anthropic' in the model selection.
# ------------------------------------------------------------------------

def download_github_repo(repo_url: str, extraction_dir: str, max_retries=3) -> str:
    """
    Downloads a GitHub repository as a ZIP file from the specified URL and extracts it.
    Tries 'main' and 'master' branches by default.
    Returns the path to the extracted repository.
    """
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

def fetch_zip(url, max_retries=3, timeout=30):
    """
    Attempts to download a ZIP file from the given URL with retries.
    Returns the raw bytes if successful, otherwise None.
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
                # Check if it's a valid zip by header or content-type
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
    """
    Attempt to detect a WordPress plugin name/version from a top-level .php file.
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

def load_custom_instructions(path="custom_instructions.txt"):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def process_repository(repo_path: str,
                       output_dir: str,
                       skip_dirs: list,
                       max_chars: int,
                       chars_per_token: int,
                       plugin_name: str = None,
                       plugin_version: str = None):
    """
    Walks the repo, reading all files (excluding skip_dirs), and merges them into
    one big string with instructions. Returns that combined string.
    """
    combined_contents = []
    included_files = []
    total_chars = 0

    # Additional AI instructions appended at the end of the combined text
    ai_instructions = load_custom_instructions()

    for root_dir, dirs, files in os.walk(repo_path, topdown=True):
        # Exclude directories in skip_dirs
        dirs[:] = [d for d in dirs if d not in skip_dirs]

        for filename in files:
            filepath = os.path.join(root_dir, filename)
            relative_path = os.path.relpath(filepath, repo_path)
            header = f"--- {relative_path} ---\n"

            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception as e:
                print(f"[DEBUG] Could not read file '{relative_path}' - {e}")
                content = "<Could not read file>"

            file_text = header + content + "\n\n"
            combined_contents.append(file_text)
            included_files.append(relative_path)
            total_chars += len(file_text)

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
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    output_filename = os.path.join(output_dir, f"{base_name}.txt")

    final_output = intro_block + "".join(combined_contents) + "".join(ai_instructions)

    with open(output_filename, "w", encoding="utf-8") as outfile:
        outfile.write(final_output)

    print(f"[DEBUG] Wrote {output_filename} with {len(final_output)} characters.")
    return final_output

def apply_function_level_change(lines, func_name, action, code, file_extension):
    """
    Dispatches to the appropriate function-level change logic depending on file type.
    """
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
    """
    Applies line-level changes (insert before/after, delete, replace).
    """
    if not line_code:
        print(f"[WARNING] Line code not provided for line-level change.")
        return lines

    matching_indices = [i for i, line in enumerate(lines) if line.strip() == line_code.strip()]
    if not matching_indices:
        print(f"[WARNING] No lines matching code '{line_code}' found. No changes applied.")
        return lines

    if len(matching_indices) > 1 and reference_line_number:
        # If multiple matches, pick the line closest to reference_line_number
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
    """
    Locates a Python function by 'def func_name(...)' and applies the specified action.
    """
    func_def_pattern = re.compile(r'^def\s+' + re.escape(func_name) + r'\s*\(')
    start_idx = None
    end_idx = None
    decorator_start = None

    # Find the function start (including decorators above it)
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

    # Indentation-based detection of end of function
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
    """
    For .js/.php, attempts to find 'function func_name(...) {' and track braces until the function ends.
    """
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
            lines = lines[:func_end_idx + 1] + new_code_lines + lines[func_end_idx + 1:]
        else:
            print(f"[WARNING] 'insert after' but no code provided for {func_name}.")
        return lines

    if action == "delete":
        del lines[start_idx:func_end_idx + 1]
        return lines

    if action == "replace":
        if code is None:
            print(f"[WARNING] 'replace' action but no code provided for {func_name}.")
            return lines
        del lines[start_idx:func_end_idx + 1]
        new_code_lines = code.splitlines(True)
        lines = lines[:start_idx] + new_code_lines + lines[start_idx:]
        return lines

    print(f"[WARNING] Unknown action '{action}' for {func_name}. No changes applied.")
    return lines

def apply_all_changes(repo_path, json_content):
    """
    Reads a JSON array of changes, applies them to the files in repo_path.
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

def load_repo_list(filename):
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            print(f"[DEBUG] Failed to load repo list from {filename}: {e}")
    return []

def save_repo_list(filename, repo_list):
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(repo_list, f)
    except Exception as e:
        print(f"[DEBUG] Failed to save repo list to {filename}: {e}")

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
    Handles the 'Download & Combine' button click:
      - Takes a URL or local path
      - Downloads/extracts if URL
      - Merges code into a single text file
      - Copies merged code to clipboard
    """
    raw_input = combine_path_var.get().strip()
    if not raw_input:
        messagebox.showerror("Error", "No URL/path provided for combining code.")
        return

    repo_list = load_repo_list(LAST_COMBINE_PATH_FILE)
    if raw_input in repo_list:
        repo_list.remove(raw_input)
    repo_list.insert(0, raw_input)
    save_repo_list(LAST_COMBINE_PATH_FILE, repo_list)
    combine_combobox['values'] = repo_list

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

    # Copy the combined code to clipboard
    root.clipboard_clear()
    root.clipboard_append(final_output)
    root.update()

    messagebox.showinfo("Success", "Code combined and copied to clipboard!\n"
                                   "Paste into ChatGPT (or other AI) for review.")

def do_apply_all_changes():
    """
    Applies JSON changes to an existing repo on disk.
    """
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

# -------------------------- GUI Setup --------------------------
root = tk.Tk()
root.title("Repo Code Changer")

# -------- Frame: Download & Combine Code --------
frame_combine = tk.LabelFrame(root, text="Download & Combine Code")
frame_combine.pack(fill=tk.X, padx=10, pady=5)

repo_list = load_repo_list(LAST_COMBINE_PATH_FILE)
combine_path_var = tk.StringVar(value=repo_list[0] if repo_list else "")
tk.Label(frame_combine, text="Repo URL/Path:").pack(side=tk.LEFT, padx=5)
combine_combobox = ttk.Combobox(frame_combine, textvariable=combine_path_var, width=50)
combine_combobox['values'] = repo_list
combine_combobox.pack(side=tk.LEFT, padx=5)
browse_btn1 = tk.Button(frame_combine, text="Browse...", command=browse_folder_for_combine)
browse_btn1.pack(side=tk.LEFT, padx=5)
combine_btn = tk.Button(frame_combine, text="Download & Combine", command=do_download_and_combine)
combine_btn.pack(side=tk.LEFT, padx=5)

# -------- Frame: User Prompt --------
frame_prompt = tk.LabelFrame(root, text="User Prompt")
frame_prompt.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

user_prompt_var = tk.Text(frame_prompt, wrap=tk.WORD, height=5)
user_prompt_var.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

# -------- Provider Selection (OpenAI vs. Deepseek vs. Anthropic vs. Ollama) --------
api_provider_var = tk.StringVar(value="openai")
frame_provider = tk.Frame(root)
frame_provider.pack(pady=5)

tk.Radiobutton(frame_provider, text="OpenAI",   variable=api_provider_var, value="openai").pack(side=tk.LEFT)
tk.Radiobutton(frame_provider, text="Deepseek", variable=api_provider_var, value="deepseek").pack(side=tk.LEFT)
tk.Radiobutton(frame_provider, text="Anthropic", variable=api_provider_var, value="anthropic").pack(side=tk.LEFT)
tk.Radiobutton(frame_provider, text="Ollama", variable=api_provider_var, value="ollama").pack(side=tk.LEFT)

# -------- Model selection dropdown (updates depending on provider) --------
def update_models(*args):
    provider = api_provider_var.get()
    model_menu = model_dropdown['menu']
    model_menu.delete(0, 'end')
    
    if provider == "openai":
        models = get_available_models_openai()
        if not models:
            models = ["gpt-3.5-turbo"]  # Fallback
    elif provider == "deepseek":
        models = ["r1"]
    elif provider == "anthropic":
        models = ["claude-3-5-haiku-20241022", "claude-3-5-sonnet-20241022"]
    elif provider == "ollama":
        models = ["gemma3:27b", "qwq"]
    else:
        models = []
    
    for m in models:
        model_menu.add_command(label=m, command=tk._setit(selected_model_var, m))
    # Set the first model in the updated list
    if models:
        selected_model_var.set(models[0])
    else:
        selected_model_var.set("")

selected_model_var = tk.StringVar()
model_dropdown = tk.OptionMenu(root, selected_model_var, "")
model_dropdown.pack(pady=5)

# Whenever the radio-button changes, refresh the model list
api_provider_var.trace("w", update_models)
update_models()  # Populate once

# -------- Button to Send to API --------
send_btn = tk.Button(root, text="Send to API", command=send_to_api)
send_btn.pack(pady=10)

# -------- Frame: Apply JSON Changes --------
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

root.mainloop()
