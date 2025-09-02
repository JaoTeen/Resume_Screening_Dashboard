# resume_screening.py
# ------------------------------------------------------------
# Tkinter Resume Screening Dashboard
#  - Uses Google Gemini for skill extraction from a JD
#  - Parses PDF/DOCX/TXT resumes (simple heuristics)
#  - TF-IDF + cosine similarity ranking
#  - Exports Excel, shows charts
#  - Adds Gmail IMAP fetch (App Password, no credentials.json)
#
# Setup:
#   pip install google-generativeai PyPDF2 python-docx docx2txt scikit-learn openpyxl matplotlib pandas
#
# API Key:
#   - Set your Gemini API key here OR via env var GEMINI_API_KEY
# Gmail:
#   - Enable IMAP in Gmail settings
#   - Use a Gmail "App Password" (not your regular password)
# ------------------------------------------------------------

import os
import re
import time
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta

import pandas as pd
import tkinter as tk
from tkinter import filedialog, ttk, messagebox

# Matplotlib (embedded in Tkinter)
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# Gemini
import google.generativeai as genai

# Resume parsing deps
import docx2txt
from PyPDF2 import PdfReader

# ML for similarity
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


# =========================
# Configuration
# =========================

# Prefer environment variable if set
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")
if GEMINI_API_KEY and GEMINI_API_KEY != "YOUR_GEMINI_API_KEY":
    genai.configure(api_key=GEMINI_API_KEY)

# Choose a Gemini model (flash = faster, pro = stronger)
GEMINI_MODEL_NAME = "gemini-1.5-flash"


# Common fallback skills
COMMON_SKILLS = [
    "python", "java", "c++", "c", "sql", "mysql", "postgresql", "mongodb",
    "django", "flask", "fastapi", "javascript", "html", "css", "react",
    "node.js", "angular", "git", "github", "docker", "kubernetes",
    "linux", "aws", "azure", "gcp",
    "numpy", "pandas", "matplotlib", "tensorflow", "pytorch",
    "problem solving", "communication", "teamwork", "leadership"
]


# =========================
# Helpers: parsing & scoring
# =========================

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EXPERIENCE_RE = re.compile(r"(\d+)\+?\s*(?:years?|yrs?)\s*(?:of)?\s*experience", re.IGNORECASE)

def clean_skill(skill: str) -> str:
    """Remove brackets, colons, trailing junk, extra spaces."""
    s = re.sub(r"[\(\)\[\]\{\}:;]", "", skill)
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def extract_name(text: str, fallback_filename: str) -> str:
    """Very light heuristic: first non-empty line with letters and spaces."""
    for line in text.splitlines():
        line = line.strip()
        if 2 <= len(line) <= 80 and re.match(r"^[A-Za-z ,.'-]+$", line):
            return line
    # fallback to filename (minus extension)
    return os.path.splitext(fallback_filename)[0]

def extract_email(text: str) -> str:
    m = EMAIL_RE.search(text)
    return m.group(0) if m else "N/A"

def extract_experience(text: str) -> str:
    m = EXPERIENCE_RE.search(text)
    if m:
        return f"{m.group(1)} years"
    # secondary heuristic: look for total experience sections
    if "experience" in text.lower():
        return "Mentioned"
    return "N/A"

def read_pdf_text(path: str) -> str:
    try:
        with open(path, "rb") as f:
            reader = PdfReader(f)
            pages = []
            for p in reader.pages:
                try:
                    t = p.extract_text() or ""
                except Exception:
                    t = ""
                pages.append(t)
            return "\n".join(pages)
    except Exception as e:
        print(f"[PDF read error] {path}: {e}")
        return ""

def read_docx_text(path: str) -> str:
    try:
        return docx2txt.process(path) or ""
    except Exception as e:
        print(f"[DOCX read error] {path}: {e}")
        return ""

def read_txt_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception as e:
        print(f"[TXT read error] {path}: {e}")
        return ""

def parse_resume(filepath: str):
    text = ""
    if filepath.lower().endswith(".pdf"):
        text = read_pdf_text(filepath)
    elif filepath.lower().endswith(".docx"):
        text = read_docx_text(filepath)
    elif filepath.lower().endswith(".txt"):
        text = read_txt_text(filepath)
    else:
        text = ""

    filename = os.path.basename(filepath)
    name = extract_name(text, filename)
    email_addr = extract_email(text)
    exp = extract_experience(text)

    return {
        "filename": filename,
        "raw_text": text,
        "name": name if name else "N/A",
        "email": email_addr,
        "experience": exp
    }

def compute_skill_similarity(parsed_resumes, selected_skills):
    jd_skills_text = " ".join(selected_skills).lower()
    resume_texts = [" ".join([kw for kw in selected_skills if kw.lower() in r["raw_text"].lower()]).lower()
                    for r in parsed_resumes]
    
    vec = TfidfVectorizer()
    tfidf = vec.fit_transform([jd_skills_text] + resume_texts)
    
    jd_vec = tfidf[0:1]
    resume_vecs = tfidf[1:]
    
    sims = cosine_similarity(resume_vecs, jd_vec).flatten()
    return sims

def classify_relevance(score: float) -> str:
    if score >= 0.75:
        return "Highly Relevant"
    elif score >= 0.5:
        return "Moderately Relevant"
    else:
        return "Low Relevance"


# =========================
# Gemini: JD → skills
# =========================

def extract_skills_from_jd(jd_text: str):
    ai_skills = []
    if GEMINI_API_KEY and GEMINI_API_KEY != "YOUR_GEMINI_API_KEY":
        try:
            model = genai.GenerativeModel(GEMINI_MODEL_NAME)
            prompt = (
                "Extract ONLY the skills, technologies, tools, and soft skills mentioned in this job description.\n"
                "Return a plain comma-separated list, no explanations, no sentences, no numbering.\n\n"
                f"{jd_text}"
            )
            response = model.generate_content(prompt)
            ai_output = (response.text or "").strip()

            # Clean Gemini output
            ai_output = re.sub(r"(?i)^(skills:|here are.*:)", "", ai_output).strip()
            parts = re.split(r"[,\n]+", ai_output)
            ai_skills = [clean_skill(p.lower()) for p in parts if p.strip()]
        except Exception as e:
            print("⚠️ Gemini error:", e)

    # Fallback: keyword match
    found = set(ai_skills)
    jd_lower = jd_text.lower()
    for skill in COMMON_SKILLS:
        if skill in jd_lower:
            found.add(skill)

    return sorted(found)


# =========================
# Gmail (IMAP) downloader
# =========================

def _decode_filename(raw):
    if not raw:
        return None
    decoded, charset = decode_header(raw)[0]
    if isinstance(decoded, bytes):
        try:
            return decoded.decode(charset or "utf-8", errors="ignore")
        except Exception:
            return decoded.decode("utf-8", errors="ignore")
    return decoded

def fetch_resumes_from_gmail(email_user: str,
                             email_pass: str,
                             filter_type: str = "unread",
                             days: int = 7,
                             save_dir: str = "resumes_from_gmail") -> list:
    """Fetch .pdf/.docx/.txt attachments via IMAP using Gmail App Password."""
    os.makedirs(save_dir, exist_ok=True)
    saved = []

    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(email_user, email_pass)
        imap.select("inbox")

        since_date = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")

        criteria = f'(SINCE "{since_date}")'
        if filter_type == "unread":
            criteria = f'(UNSEEN SINCE "{since_date}")'
        elif filter_type == "read":
            criteria = f'(SEEN SINCE "{since_date}")'

        status, data = imap.search(None, criteria)
        if status != "OK":
            raise RuntimeError("IMAP search failed")

        ids = data[0].split()
        for msg_id in ids:
            status, msg_data = imap.fetch(msg_id, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])

            for part in msg.walk():
                # attachments only
                disp = (part.get("Content-Disposition") or "").lower()
                filename = _decode_filename(part.get_filename())
                if "attachment" not in disp and not filename:
                    continue

                if not filename:
                    continue
                lower = filename.lower()
                if not (lower.endswith(".pdf") or lower.endswith(".docx") or lower.endswith(".txt")):
                    continue

                payload = part.get_payload(decode=True)
                if not payload:
                    continue

                # handle duplicate filenames
                out_path = os.path.join(save_dir, filename)
                base, ext = os.path.splitext(out_path)
                c = 1
                while os.path.exists(out_path):
                    out_path = f"{base}_{c}{ext}"
                    c += 1

                with open(out_path, "wb") as f:
                    f.write(payload)
                saved.append(out_path)

        imap.logout()
    except Exception as e:
        messagebox.showerror("Gmail Error", f"Failed to fetch from Gmail:\n{e}")

    return saved


# =========================
# Tkinter App 
# =========================

class ResumeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Resume Screening Dashboard (Gemini)")
        self.root.configure(bg="#1e1e1e")
        self.root.geometry("1100x700")

        # Existing state
        self.resume_dir = None
        self.jd_text = ""
        self.skills = []
        self.skill_vars = []

        # --- Added state for Gmail ---
        self.source_choice = tk.StringVar(value="folder")   # 'folder' or 'gmail'
        self.mail_filter = tk.StringVar(value="unread")     # 'unread'/'read'/'both'
        self.days_filter = tk.IntVar(value=7)               # 1/7/15/30
        self.gmail_user_entry = None
        self.gmail_pass_entry = None
        self.gmail_download_dir = os.path.join(os.getcwd(), "resumes_from_gmail")

        self.build_file_selection_ui()

    def build_file_selection_ui(self):
        for widget in self.root.winfo_children():
            widget.destroy()

        frame = tk.Frame(self.root, bg="#1e1e1e")
        frame.pack(pady=20, padx=20, fill="x")

        # --- New: Source toggle ---
        tk.Label(frame, text="Resume Source:", fg="white", bg="#1e1e1e", font=("Arial", 11, "bold")).grid(row=0, column=0, sticky="w", pady=5)
        tk.Radiobutton(frame, text="Folder", variable=self.source_choice, value="folder",
                       command=lambda: self._render_source_area(frame),
                       fg="white", bg="#1e1e1e", selectcolor="#333").grid(row=0, column=1, sticky="w", padx=(8, 0))
        tk.Radiobutton(frame, text="Gmail (IMAP)", variable=self.source_choice, value="gmail",
                       command=lambda: self._render_source_area(frame),
                       fg="white", bg="#1e1e1e", selectcolor="#333").grid(row=0, column=2, sticky="w", padx=(8, 0))

        # container that changes depending on source
        self.source_area = tk.Frame(frame, bg="#1e1e1e")
        self.source_area.grid(row=1, column=0, columnspan=3, sticky="w")
        self._render_source_area(frame)

        # Job Description (textbox + upload button)
        # tk.Label(frame, text="Job Description:", fg="white", bg="#1e1e1e", font=("Arial", 11)).grid(row=2, column=0, sticky="nw", pady=5)

        self.jd_textbox = tk.Text(frame, height=12, width=60, bg="#2d2d2d", fg="white", insertbackground="white", wrap="word")
        self.jd_textbox.grid(row=6, column=1, padx=5, pady=5, sticky="w")

        tk.Button(frame, text="Upload JD File: ", command=self.upload_jd_file, bg="#333", fg="white").grid(row=6, column=0, padx=10, pady=5, sticky="n")

        # Next button
        tk.Button(frame, text="Next → Extract Skills", command=self.extract_and_select_skills, bg="#007acc", fg="white").grid(row=7, column=1, pady=15, sticky="e")

        # Footer tip
        tip = "Tip: You can paste folder path or JD text manually, or use Browse/Upload."
        tk.Label(self.root, text=tip, fg="#cccccc", bg="#1e1e1e", font=("Arial", 9, "italic")).pack(pady=5)

    def _render_source_area(self, frame_parent):
        """Renders the dynamic part of the first screen (minimal impact to your code)."""
        for w in self.source_area.winfo_children():
            w.destroy()

        if self.source_choice.get() == "folder":
            # Resume Folder (textbox + browse button) — same as your original layout
            # tk.Label(self.source_area, text="Resume Folder:", fg="white", bg="#1e1e1e", font=("Arial", 11)).grid(row=0, column=0, sticky="w", pady=5)
            self.resume_entry = tk.Entry(self.source_area, width=60, bg="#2d2d2d", fg="white", insertbackground="white")
            self.resume_entry.grid(row=0, column=2, padx=10, pady=5, sticky="w")
            tk.Button(self.source_area, text="Browse Folder: ", command=self.select_resume_folder, bg="#333", fg="white").grid(row=0, column=0, padx=15, pady=5)
        else:
            # Gmail UI (new) — also create a read-only resume_entry so your later code can still read it
            tk.Label(self.source_area, text="Gmail Address:", fg="white", bg="#1e1e1e").grid(row=0, column=0, sticky="w", pady=3)
            self.gmail_user_entry = tk.Entry(self.source_area, width=40, bg="#2d2d2d", fg="white", insertbackground="white")
            self.gmail_user_entry.grid(row=0, column=1, sticky="w", padx=5, pady=5)

            tk.Label(self.source_area, text="App Password:", fg="white", bg="#1e1e1e").grid(row=1, column=0, sticky="w", pady=3)
            self.gmail_pass_entry = tk.Entry(self.source_area, width=40, show="*", bg="#2d2d2d", fg="white", insertbackground="white")
            self.gmail_pass_entry.grid(row=1, column=1, sticky="w", padx=5, pady=5)

            tk.Label(self.source_area, text="Mail Filter:", fg="white", bg="#1e1e1e").grid(row=2, column=0, sticky="w", pady=3)
            tk.OptionMenu(self.source_area, self.mail_filter, "unread", "read", "both").grid(row=2, column=1, sticky="w", padx=5, pady=5)

            tk.Label(self.source_area, text="Lookback Days:", fg="white", bg="#1e1e1e").grid(row=3, column=0, sticky="w", pady=3)
            tk.OptionMenu(self.source_area, self.days_filter, 1, 7, 15, 30).grid(row=3, column=1, sticky="w", padx=5, pady=5)

            # read-only target folder entry so downstream code continues to work unchanged
            tk.Label(self.source_area, text="Download to:", fg="white", bg="#1e1e1e").grid(row=4, column=0, sticky="w", pady=3)
            self.resume_entry = tk.Entry(self.source_area, width=60, bg="#2d2d2d", fg="#cccccc", insertbackground="white")
            self.resume_entry.grid(row=4, column=1, padx=5, pady=10, sticky="w")
            self.resume_entry.insert(0, self.gmail_download_dir)

    def select_resume_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.resume_entry.delete(0, tk.END)
            self.resume_entry.insert(0, folder)
            self.resume_dir = folder

    def upload_jd_file(self):
        filepath = filedialog.askopenfilename(filetypes=[("Text files", "*.txt"), ("Word files", "*.docx"), ("All files", "*.*")])
        if filepath:
            try:
                if filepath.endswith(".docx"):
                    jd_text = docx2txt.process(filepath)
                else:
                    with open(filepath, "r", encoding="utf-8") as f:
                        jd_text = f.read()
                self.jd_textbox.delete("1.0", tk.END)
                self.jd_textbox.insert(tk.END, jd_text)
            except Exception as e:
                messagebox.showerror("Error", f"Could not load JD file: {e}")

    def show_skill_selection_ui(self):
        for widget in self.root.winfo_children():
            widget.destroy()

        tk.Label(self.root, text="Select Important Skills", fg="white", bg="#1e1e1e",
                 font=("Arial", 13, "bold")).pack(pady=10)

        # Scrollable frame for many skills
        container = tk.Frame(self.root, bg="#1e1e1e")
        container.pack(fill="both", expand=False, padx=10)
        canvas = tk.Canvas(container, bg="#1e1e1e", highlightthickness=0)
        scrollbar = tk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg="#1e1e1e")
        scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set, height=260)

        self.skill_vars = []
        cols = 4
        for i, skill in enumerate(self.skills):
            var = tk.BooleanVar(value=True)
            self.skill_vars.append((skill, var))
            chk = tk.Checkbutton(scroll_frame, text=skill, variable=var, fg="white",
                                 bg="#1e1e1e", selectcolor="#333", activebackground="#1e1e1e")
            chk.grid(row=i // cols, column=i % cols, sticky="w", padx=10, pady=5)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        btns = tk.Frame(self.root, bg="#1e1e1e")
        btns.pack(pady=12)
        tk.Button(btns, text="Back", command=self.build_file_selection_ui, bg="#333", fg="white").pack(side="left", padx=8)
        tk.Button(btns, text="Process Resumes", command=self.process_resumes, bg="#007acc", fg="white").pack(side="left", padx=8)

    def extract_and_select_skills(self):
        # Save values before destroying widgets
        self.resume_dir = self.resume_entry.get().strip() if self.resume_entry else None
        self.jd_text = self.jd_textbox.get("1.0", tk.END).strip()

        # Gmail mode: fetch emails first into self.resume_dir, then continue
        if self.source_choice.get() == "gmail":
            user = (self.gmail_user_entry.get().strip() if self.gmail_user_entry else "")
            pwd = (self.gmail_pass_entry.get().strip() if self.gmail_pass_entry else "")
            if not user or not pwd:
                messagebox.showerror("Error", "Please enter Gmail address and App Password.")
                return

            # Fetch via IMAP
            save_dir = self.gmail_download_dir
            files = fetch_resumes_from_gmail(
                email_user=user,
                email_pass=pwd,
                filter_type=self.mail_filter.get(),
                days=int(self.days_filter.get() or 7),
                save_dir=save_dir
            )
            if not files:
                messagebox.showwarning("No Resumes", "No attachments (.pdf/.docx/.txt) found in matching emails.")
                return

            # Update the folder path so the rest of your pipeline stays the same
            self.resume_dir = save_dir
            if self.resume_entry:
                self.resume_entry.delete(0, tk.END)
                self.resume_entry.insert(0, save_dir)

        # Folder-mode validation (kept same)
        if self.source_choice.get() == "folder":
            if not self.resume_dir or not os.path.isdir(self.resume_dir):
                messagebox.showerror("Error", "Please select a valid Resume Folder.")
                return

        if not self.jd_text:
            messagebox.showerror("Error", "Please upload or paste a Job Description.")
            return

        # Extract skills (unchanged)
        self.skills = extract_skills_from_jd(self.jd_text)
        if not self.skills:
            messagebox.showwarning("No Skills Found", "No skills could be extracted from the JD.")
        self.show_skill_selection_ui()

    def process_resumes(self):
        # now self.resume_dir and self.jd_text are already set
        # (Your original placeholder left here; safe because extract_and_select_skills sets resume_dir)
        _ = os.listdir(self.resume_dir) if self.resume_dir and os.path.isdir(self.resume_dir) else []

        # self.resume_dir is already set in extract_and_select_skills
        if not self.resume_dir or not os.path.isdir(self.resume_dir):
            messagebox.showwarning("No resumes", "Please enter or select a valid resume folder.")
            return

        selected_skills = [s for s, v in self.skill_vars if v.get()] if self.skill_vars else []

        # Collect files
        resumes = [os.path.join(self.resume_dir, f) for f in os.listdir(self.resume_dir)
                   if f.lower().endswith((".pdf", ".docx", ".txt"))]
        if not resumes:
            messagebox.showwarning("No files", "No .pdf, .docx, or .txt files found in the selected folder.")
            return

        # Parse
        parsed_resumes = [parse_resume(r) for r in resumes]

        # Score
        scores = compute_skill_similarity(parsed_resumes, selected_skills)

        # Build rows
        rows = []
        for r, score in zip(parsed_resumes, scores):
            text_lower = r["raw_text"].lower()
            present = [kw for kw in selected_skills if kw.lower() in text_lower]
            missing = [kw for kw in selected_skills if kw.lower() not in text_lower]
            rows.append({
                "filename": r["filename"],
                "name": r["name"],
                "email": r["email"],
                "experience": r["experience"],
                "matched_skills": ", ".join(sorted(set(present))),
                "missing_skills": ", ".join(sorted(set(missing))),
                "score (%)": round(float(score) * 100, 2),
                "classification": classify_relevance(float(score))
            })

        df = pd.DataFrame(rows).sort_values(by="score (%)", ascending=False)

        # Export
        timestamp = int(time.time())
        all_path = f"all_resumes_{timestamp}.xlsx"
        top_path = f"top20_resumes_{timestamp}.xlsx"
        try:
            df.to_excel(all_path, index=False)
            df.head(20).to_excel(top_path, index=False)
        except Exception as e:
            print(f"⚠️ Excel export failed: {e}")

        self.show_results_ui(df.head(20), selected_skills, [all_path, top_path])

    def show_results_ui(self, top20: pd.DataFrame, skills: list, exported_paths: list):
        for widget in self.root.winfo_children():
            widget.destroy()

        # Treeview (grid)
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview",
                        background="#1e1e1e",
                        foreground="white",
                        fieldbackground="#1e1e1e",
                        rowheight=24,
                        borderwidth=0)
        style.map("Treeview", background=[("selected", "#007acc")])

        tree = ttk.Treeview(self.root, columns=list(top20.columns), show="headings", height=12)
        tree.pack(fill="both", expand=False, pady=10, padx=10)

        for col in top20.columns:
            tree.heading(col, text=col)
            tree.column(col, width=140, anchor="center")

        for _, row in top20.iterrows():
            tree.insert("", tk.END, values=list(row.values))

        # =========================
        # Horizontal Bar Charts
        # =========================
        fig = plt.figure(figsize=(12, 6), facecolor="#1e1e1e")

        # 1. Score distribution
        ax1 = fig.add_subplot(1, 2, 1)
        ax1.barh(top20["filename"], top20["score (%)"], color="#1f77b4")
        ax1.set_xlabel("Score %", color="white")
        ax1.set_title("Top 20 Resume Scores", color="white")
        ax1.tick_params(axis="x", colors="white")
        ax1.tick_params(axis="y", colors="white")
        ax1.invert_yaxis()  # highest score on top

        # 2. Skill coverage
        ax2 = fig.add_subplot(1, 2, 2)
        skill_counts = {s: 0 for s in skills}
        for s in top20["matched_skills"]:
            present_list = [x.strip().lower() for x in s.split(",")] if isinstance(s, str) and s else []
            for sk in skills:
                if sk.lower() in present_list:
                    skill_counts[sk] += 1

        ax2.barh(list(skill_counts.keys()), list(skill_counts.values()), color="#ff7f0e")
        ax2.set_xlabel("Number of Resumes", color="white")
        ax2.set_title("Skill Coverage in Top 20", color="white")
        ax2.tick_params(axis="x", colors="white")
        ax2.tick_params(axis="y", colors="white")
        ax2.invert_yaxis()  # most frequent skill on top

        plt.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=self.root)
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=10)
        canvas.draw()

        # Buttons/footer
        footer = tk.Frame(self.root, bg="#1e1e1e")
        footer.pack(pady=8)
        tk.Button(footer, text="← Back", command=self.build_file_selection_ui, bg="#333", fg="white").pack(side="left", padx=6)

        exported_label = " | ".join([os.path.basename(p) for p in exported_paths if os.path.exists(p)])
        if exported_label:
            tk.Label(footer, text=f"Exported: {exported_label}", fg="#b0e0b0", bg="#1e1e1e").pack(side="left", padx=12)


# =========================
# Main
# =========================

if __name__ == "__main__":
    if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_GEMINI_API_KEY":
        print("⚠️ Warning: GEMINI_API_KEY not set. Skill extraction will rely on fallback matching only.")
        print("   Set the key via environment:  export GEMINI_API_KEY='your_key_here'")
        print("   or edit GEMINI_API_KEY in this script.\n")

    root = tk.Tk()
    app = ResumeApp(root)
    root.mainloop()
