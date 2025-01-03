import os
import json
import tkinter as tk
from tkinter import filedialog, messagebox

# Name of the file in which we store the last-used folder path:
LAST_PATH_FILE = 'last_repo_path.txt'

def load_last_path():
    """
    Load the last repository path from a text file, if it exists.
    Return an empty string if not found.
    """
    if os.path.exists(LAST_PATH_FILE):
        try:
            with open(LAST_PATH_FILE, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except:
            pass
    return ""

def save_last_path(path):
    """
    Save the specified repository path to a text file.
    """
    try:
        with open(LAST_PATH_FILE, 'w', encoding='utf-8') as f:
            f.write(path.strip())
    except:
        pass

def apply_changes(repo_path, json_content):
    """
    Parse the JSON content (array of changes) and apply them to files in repo_path.
    The JSON structure is expected to be an array of objects with:
        file, line, action, content (if needed)
    Actions can be "insert before", "insert after", "delete", "replace".
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
        # Validate structure
        if not isinstance(change, dict):
            continue
        required_keys = ["file", "line", "action"]
        if not all(k in change for k in required_keys):
            continue

        file_rel = change["file"]
        line_num = change["line"]  # expected to be int
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

        # Apply the change
        if action == "delete":
            # Delete the line at line_num (1-based)
            if 1 <= line_num <= len(lines):
                lines.pop(line_num - 1)
            else:
                print(f"[WARNING] Invalid line number {line_num} for delete in {file_rel}.")
        elif action == "replace":
            # Replace the line at line_num (1-based)
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
                # Insert before line_num => index (line_num - 1)
                index = max(0, line_num - 1)
                lines.insert(index, content + "\n")
        elif action == "insert after":
            if content is None:
                print(f"[WARNING] 'insert after' action without 'content' in {file_rel}.")
            else:
                # Insert after line_num => index (line_num)
                index = min(line_num, len(lines))
                lines.insert(index, content + "\n")
        else:
            print(f"[WARNING] Unknown action '{action}' in {file_rel}. Skipping.")

        # Write file lines back
        try:
            with open(target_file, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            print(f"[INFO] Changes applied to {file_rel}")
        except Exception as e:
            print(f"[ERROR] Could not write file '{target_file}' - {e}")

def browse_folder():
    """
    Let the user browse for a folder, then set the entry text.
    """
    folder_selected = filedialog.askdirectory()
    if folder_selected:
        folder_path_var.set(folder_selected)

def on_apply_changes():
    """
    Called when user clicks "Apply Changes".
    1. Get the repo path and save it for future sessions.
    2. Get the JSON from text box.
    3. Apply changes.
    """
    repo_path = folder_path_var.get().strip()
    if not repo_path:
        messagebox.showerror("Error", "No repository path provided.")
        return
    if not os.path.isdir(repo_path):
        messagebox.showerror("Error", "The specified path is not a directory.")
        return

    # Save the path
    save_last_path(repo_path)

    # Get JSON and process
    json_input = text_json.get("1.0", tk.END).strip()
    if not json_input:
        messagebox.showwarning("No JSON", "Please paste JSON instructions.")
        return

    apply_changes(repo_path, json_input)

def main():
    # Create the UI
    root = tk.Tk()
    root.title("Code Repository Changer")

    # Frame for path input
    path_frame = tk.Frame(root)
    path_frame.pack(fill=tk.X, padx=10, pady=5)

    tk.Label(path_frame, text="Repository Folder Path:").pack(side=tk.LEFT)
    global folder_path_var
    folder_path_var = tk.StringVar(value=load_last_path())

    path_entry = tk.Entry(path_frame, textvariable=folder_path_var, width=50)
    path_entry.pack(side=tk.LEFT, padx=5)

    browse_button = tk.Button(path_frame, text="Browse...", command=browse_folder)
    browse_button.pack(side=tk.LEFT, padx=5)

    # Frame for JSON text
    json_frame = tk.Frame(root)
    json_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    tk.Label(json_frame, text="Paste the JSON changes below:").pack(anchor=tk.W)

    global text_json
    text_json = tk.Text(json_frame, wrap=tk.NONE, width=80, height=20)
    text_json.pack(fill=tk.BOTH, expand=True)

    # Frame for actions
    action_frame = tk.Frame(root)
    action_frame.pack(fill=tk.X, padx=10, pady=5)

    apply_button = tk.Button(action_frame, text="Apply Changes", command=on_apply_changes)
    apply_button.pack(side=tk.RIGHT)

    root.mainloop()

if __name__ == "__main__":
    main()
