from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_from_directory
import google.generativeai as genai
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import uuid, socket, os, json, random, string, time, PyPDF2, pytz
from datetime import datetime
from google.generativeai.types import HarmCategory, HarmBlockThreshold
from sqlalchemy import event
import shutil

# 1. SETUP
load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'talentflow_secret_key_2025')

# 2. AI CONFIG
api_key = os.getenv('GOOGLE_API_KEY')
if not api_key: print("⚠️ MISSING API KEY!")
else: genai.configure(api_key=api_key)

SAFETY_SETTINGS = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}

def get_ai_model():
    return genai.GenerativeModel('gemini-flash-lite-latest') 

model = get_ai_model()

# 3. DB & STORAGE
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URI', 'sqlite:///talentflow.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ================= MODELS =================

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='recruiter')
    full_name = db.Column(db.String(100))
    interviews = db.relationship('Interview', backref='recruiter', cascade="all, delete-orphan", lazy=True)

class Interview(db.Model):
    id = db.Column(db.String(4), primary_key=True)
    recruiter_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    field = db.Column(db.String(100), nullable=False)
    base_questions = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.String(20))
    candidates = db.relationship('Candidate', backref='interview_room', cascade="all, delete-orphan", lazy=True)

class Candidate(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    room_id = db.Column(db.String(4), db.ForeignKey('interview.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), nullable=False)
    videos = db.relationship('Video', backref='candidate', cascade="all, delete-orphan", lazy=True)
    
    # --- UPDATE: Thêm cột lưu đường dẫn folder ---
    folder_path = db.Column(db.String(255), nullable=True)
    
    cv_filename = db.Column(db.String(200), nullable=True)
    personal_questions = db.Column(db.Text, nullable=True) 
    overall_analysis = db.Column(db.Text, nullable=True)
    videos = db.relationship('Video', backref='candidate', cascade="all, delete-orphan", lazy=True)

class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    candidate_id = db.Column(db.String(36), db.ForeignKey('candidate.id'), nullable=False)
    question_index = db.Column(db.Integer, nullable=False)
    filename = db.Column(db.String(200), nullable=False)
    ai_score = db.Column(db.Float, default=0.0)
    ai_summary = db.Column(db.Text, default="")


@event.listens_for(Candidate, 'after_delete')
def delete_candidate_files(mapper, connection, target):
    if target.folder_path:
        folder_to_delete = os.path.join(app.config['UPLOAD_FOLDER'], target.folder_path)
        if os.path.exists(folder_to_delete):
            try:
                shutil.rmtree(folder_to_delete)
                print(f"🗑️ Đã xóa sạch folder: {folder_to_delete}")
            except Exception as e:
                print(f"⚠️ Lỗi xóa folder: {e}")

# ================= AI LOGIC =================

def clean_json_text(text):
    try:
        text = text.replace('```json', '').replace('```', '').strip()
        start, end = text.find('['), text.rfind(']') + 1
        if start != -1 and end != -1: return json.loads(text[start:end])
        start, end = text.find('{'), text.rfind('}') + 1
        if start != -1 and end != -1: return json.loads(text[start:end])
        return json.loads(text)
    except: return None

def ai_generate_questions_with_criteria(job_title, count=5):
    print(f"🤖 AI Generating {count} Q&A for {job_title}...")
    try:
        prompt = f"""
        Role: Senior Recruiter. Task: Create exactly {count} interview questions for "{job_title}".
        For each question, define a specific "Scoring Criteria" (what to look for in the answer).
        
        OUTPUT FORMAT (JSON Array of Objects):
        [
            {{ "question": "Question 1 text...", "criteria": "Criteria for Q1..." }},
            {{ "question": "Question 2 text...", "criteria": "Criteria for Q2..." }}
        ]
        """
        response = model.generate_content(prompt)
        data = clean_json_text(response.text)
        return data[:count] if isinstance(data, list) else []
    except Exception as e:
        print(f"❌ AI Gen Error: {e}")
        return [{"question": "Tell us about yourself.", "criteria": "Confidence, clarity, and relevance."}]

def ai_generate_cv_questions(cv_text, job_title):
    try:
        prompt = f"""
        Role: {job_title}. CV Excerpt: "{cv_text[:3000]}..."
        Generate 2 specific questions based on this CV. Include criteria.
        Output JSON: [{{ "question": "...", "criteria": "..." }}, ...]
        """
        response = model.generate_content(prompt)
        data = clean_json_text(response.text)
        return data[:2] if isinstance(data, list) else []
    except: return []

def ai_grade_single_video(video_path, question, criteria):
    if not os.path.exists(video_path): return {"score": 0, "summary": "Video missing."}
    try:
        print(f"🤖 Grading: {question[:30]}...")
        # Upload file lên Gemini
        video_file = genai.upload_file(path=video_path, mime_type="video/webm")
        
        # Retry logic for processing
        for _ in range(10): 
            time.sleep(2)
            video_file = genai.get_file(video_file.name)
            if video_file.state.name == "ACTIVE": break
            if video_file.state.name == "FAILED": raise ValueError("Video failed")
        
        prompt = f"""
        Role: Interviewer. 
        Question: "{question}"
        Criteria: "{criteria}"
        
        Task: Watch the video.
        1. If silent/no answer: Score 0.
        2. Evaluate based on Criteria.
        
        Output JSON: {{ "summary": "Feedback...", "score": 8.5 }}
        """
        response = model.generate_content([video_file, prompt], generation_config={"response_mime_type": "application/json"})
        try: genai.delete_file(video_file.name)
        except: pass
        parsed = clean_json_text(response.text)
        return parsed if parsed else {"score": 0, "summary": "AI Error: Invalid format"}
    except Exception as e:
        print(f"❌ Grading Error: {e}")
        return {"score": 0, "summary": "AI Error. Please check manually."}

def ai_generate_overall_report(candidate_name, role, qa_results):
    print("🤖 Generating Overall Report...")
    try:
        summary_text = f"Candidate: {candidate_name}\nRole: {role}\n\nPerformance:\n"
        for res in qa_results:
            summary_text += f"- Q: {res['question']}\n  Score: {res['score']}\n  Feedback: {res['summary']}\n\n"
            
        prompt = f"""
        Analyze this interview performance.
        {summary_text}
        
        Provide a JSON summary with:
        1. "suitability": "High", "Medium", or "Low".
        2. "strengths": List of 2-3 key strengths.
        3. "weaknesses": List of 2-3 areas to improve.
        4. "final_comment": A professional paragraph summarizing the candidate.
        
        Output JSON Only.
        """
        response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(response.text)
    except Exception as e:
        print(f"❌ Overall Report Error: {e}")
        return None

def extract_text_from_pdf(path):
    try:
        reader = PyPDF2.PdfReader(path)
        return "".join([p.extract_text() for p in reader.pages])[:5000]
    except: return ""

# ================= ROUTES =================

@app.route('/')
def home(): return render_template('home.html')

# --- AUTH ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and check_password_hash(user.password_hash, request.form.get('password')):
            session['user_id'] = user.id
            session['role'] = user.role
            session['name'] = user.full_name
            return redirect(url_for('manager_dashboard' if user.role == 'manager' else 'recruiter_dashboard'))
        return render_template('login.html', error="Invalid Credentials")
    return render_template('login.html')

@app.route('/manager', methods=['GET', 'POST'])
def manager_dashboard():
    if session.get('role') != 'manager': return redirect(url_for('login'))
    if request.method == 'POST':
        uname = request.form.get('username')
        if User.query.filter_by(username=uname).first():
            return render_template('manager_dashboard.html', error="User exists!", users=User.query.filter_by(role='recruiter').all())
        db.session.add(User(username=uname, password_hash=generate_password_hash(request.form.get('password')), full_name=request.form.get('fullname'), role='recruiter'))
        db.session.commit()
        return redirect(url_for('manager_dashboard'))
    return render_template('manager_dashboard.html', users=User.query.filter_by(role='recruiter').all())

@app.route('/delete_user/<int:uid>')
def delete_user(uid):
    if session.get('role') != 'manager': 
        return redirect(url_for('login'))
    user = db.session.get(User, uid)
    if user and user.id != session.get('user_id'):
        db.session.delete(user)
        db.session.commit()
    return redirect(url_for('manager_dashboard'))

# --- RECRUITER DASHBOARD ---
@app.route('/dashboard', methods=['GET', 'POST'])
def recruiter_dashboard():
    if session.get('role') != 'recruiter': return redirect(url_for('login'))
    
    if request.method == 'POST':
        field = request.form.get('field')
        mode = request.form.get('mode')
        final_qs = []
        
        if mode == 'manual':
            q_list = request.form.getlist('manual_qs[]')
            c_list = request.form.getlist('manual_cs[]')
            for q, c in zip(q_list, c_list):
                if q.strip(): final_qs.append({"question": q.strip(), "criteria": c.strip() or "General assessment"})
        else:
            try: count = int(request.form.get('count', 5))
            except: count = 5
            final_qs = ai_generate_questions_with_criteria(field, count)
            
        rid = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        
        db.session.add(Interview(
            id=rid, 
            recruiter_id=session['user_id'], 
            field=field, 
            base_questions=json.dumps(final_qs),
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M")
        ))
        db.session.commit()
        return redirect(url_for('recruiter_dashboard'))

    my_interviews = Interview.query.filter_by(recruiter_id=session['user_id']).order_by(Interview.created_at.desc()).all()
    dashboard_data = []
    for i in my_interviews:
        cands = Candidate.query.filter_by(room_id=i.id).all()
        cand_list = [{"id": c.id, "name": c.name, "email": c.email, "count": Video.query.filter_by(candidate_id=c.id).count()} for c in cands]
        
        try: q_len = len(json.loads(i.base_questions))
        except: q_len = 0
            
        dashboard_data.append({"id": i.id, "field": i.field, "date": i.created_at, "question_count": q_len, "candidates": cand_list})
        
    return render_template('recruiter_dashboard.html', data=dashboard_data, recruiter_name=session.get('name'))

@app.route('/report/<cid>')
def view_report(cid):
    if not session.get('user_id'): return redirect(url_for('login'))
    cand = db.session.get(Candidate, cid)
    room = db.session.get(Interview, cand.room_id)
    
    questions_data = json.loads(cand.personal_questions)
    videos = Video.query.filter_by(candidate_id=cand.id).all()
    
    results = {}
    total_score = 0
    qa_results_for_ai = []
    
    for v in videos:
        # --- UPDATE: Đường dẫn vật lý đầy đủ để AI đọc ---
        # Nếu là dữ liệu cũ (chưa có folder_path), fallback về thư mục gốc (tùy chọn)
        folder = cand.folder_path if cand.folder_path else ""
        video_full_path = os.path.join(app.config['UPLOAD_FOLDER'], folder, v.filename)
        
        # --- UPDATE: Đường dẫn web để hiển thị HTML ---
        # Sử dụng forward slash cho URL: folder/filename
        web_path = f"{folder}/{v.filename}" if folder else v.filename

        if v.ai_score == 0 and not v.ai_summary:
            q_data = questions_data[v.question_index]
            ai_out = ai_grade_single_video(
                video_full_path, 
                q_data['question'], 
                q_data.get('criteria', '')
            )
            v.ai_score, v.ai_summary = ai_out.get('score', 0), ai_out.get('summary', '')
            db.session.commit()
        
        results[str(v.question_index)] = {"score": v.ai_score, "summary": v.ai_summary, "file": web_path}
        total_score += v.ai_score
        qa_results_for_ai.append({
            "question": questions_data[v.question_index]['question'],
            "score": v.ai_score,
            "summary": v.ai_summary
        })

    overall = None
    if len(videos) == len(questions_data):
        if not cand.overall_analysis:
            overall = ai_generate_overall_report(cand.name, room.field, qa_results_for_ai)
            if overall:
                cand.overall_analysis = json.dumps(overall)
                db.session.commit()
        else:
            overall = json.loads(cand.overall_analysis)

    return render_template('report.html', 
                          candidate=cand, 
                          room=room, 
                          results=results, 
                          total=round(total_score,1), 
                          max=len(questions_data)*10, 
                          questions=questions_data, 
                          overall=overall)

# --- CANDIDATE FLOW ---
@app.route('/candidate', methods=['GET', 'POST'])
def candidate_portal():
    if request.method == 'POST':
        rid = request.form.get('room_id').upper().strip()
        email = request.form.get('email').strip().lower()
        raw_name = request.form.get('name').strip()
        
        room = db.session.get(Interview, rid)
        if not room: return render_template('candidate_portal.html', error="Invalid Room Code")
        
        # Check duplicate
        exist = Candidate.query.filter_by(room_id=rid, email=email).first()
        if exist:
            if exist.name.lower() == raw_name.lower():
                session['cid'] = exist.id
                return redirect(url_for('candidate_review'))
            else: return render_template('candidate_portal.html', error="Email already used by another name!")

        # --- UPDATE: Tạo cấu trúc Folder DD_MM_YYYY_HH_mm_user ---
        vn_tz = pytz.timezone('Asia/Bangkok')
        timestamp = datetime.now(vn_tz).strftime("%d_%m_%Y_%H_%M")
        safe_name = secure_filename(raw_name).replace("-", "_")
        if not safe_name: safe_name = "user"
        
        user_folder_name = f"{timestamp}_{safe_name}"
        full_folder_path = os.path.join(app.config['UPLOAD_FOLDER'], user_folder_name)
        os.makedirs(full_folder_path, exist_ok=True)
        # --------------------------------------------------------

        # CV Handle
        cv_file = request.files.get('cv_file')
        cv_text = ""
        cv_name = None
        if cv_file and cv_file.filename:
            # Lưu CV vào folder con với tên chuẩn
            ext = cv_file.filename.rsplit('.', 1)[1].lower() if '.' in cv_file.filename else 'pdf'
            cv_name = f"CV.{ext}"
            cv_path = os.path.join(full_folder_path, cv_name)
            cv_file.save(cv_path)
            cv_text = extract_text_from_pdf(cv_path)

        # Merge Questions
        base_qs = json.loads(room.base_questions)
        final_qs = base_qs.copy()
        
        if cv_text:
            cv_qs = ai_generate_cv_questions(cv_text, room.field)
            for q in cv_qs:
                if isinstance(q, str): 
                    final_qs.append({"question": q, "criteria": "Based on CV verification."})
                elif isinstance(q, dict):
                    final_qs.append(q)

        cid = str(uuid.uuid4())
        session['cid'] = cid
        
        # Lưu folder_path vào DB
        db.session.add(Candidate(
            id=cid, 
            room_id=rid, 
            name=raw_name, 
            email=email, 
            folder_path=user_folder_name, # <-- Cột mới
            cv_filename=cv_name, 
            personal_questions=json.dumps(final_qs)
        ))
        db.session.commit()
        return redirect(url_for('interview_room'))
    return render_template('candidate_portal.html')

@app.route('/interview')
def interview_room():
    if not session.get('cid'): return redirect(url_for('candidate_portal'))
    cand = db.session.get(Candidate, session.get('cid'))
    qs_data = json.loads(cand.personal_questions)
    simple_qs = [q['question'] for q in qs_data]
    return render_template('interview.html', questions=simple_qs)

@app.route('/candidate/review')
def candidate_review():
    if not session.get('cid'): return redirect(url_for('home'))
    cand = db.session.get(Candidate, session.get('cid'))
    videos = Video.query.filter_by(candidate_id=cand.id).all()
    v_dict = {str(v.question_index): v.filename for v in videos}
    
    qs_data = json.loads(cand.personal_questions)
    simple_qs = [q['question'] for q in qs_data]
    
    return render_template('candidate_review.html', candidate=cand, questions=simple_qs, videos=v_dict)

@app.route('/upload_video', methods=['POST'])
def upload_video():
    cid = session.get('cid')
    if not cid: return jsonify({"status": "error"}), 400
    file = request.files['video']
    idx = request.form.get('question_index')
    
    cand = db.session.get(Candidate, cid)

    if file and cand:
        # --- UPDATE: Lưu vào folder riêng với tên ngắn gọn ---
        # Tên file: Q1.webm, Q2.webm (idx bắt đầu từ 0 nên +1 cho thân thiện)
        fname = f"Q{int(idx) + 1}.webm"
        
        # Đường dẫn: uploads/FOLDER_USER/Q1.webm
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], cand.folder_path, fname)
        file.save(save_path)
        
        exist = Video.query.filter_by(candidate_id=cid, question_index=int(idx)).first()
        if exist: 
            exist.filename = fname
            exist.ai_score = 0 
            exist.ai_summary = ""
        else: db.session.add(Video(candidate_id=cid, question_index=int(idx), filename=fname))
        
        db.session.commit()
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 500

# --- UPDATE: Serve file từ folder con (Nested paths) ---
@app.route('/uploads/<path:filename>')
def uploaded_file(filename): 
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('home'))

def init_db():
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(role='manager').first():
            db.session.add(User(username='manager', password_hash=generate_password_hash('admin123'), full_name='System Admin', role='manager'))
            db.session.commit()
            print("✅ DB Init Success")

if __name__ == '__main__': init_db(); app.run(host='0.0.0.0', port=5000, debug=True)