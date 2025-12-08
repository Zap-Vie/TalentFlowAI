from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_from_directory
import google.generativeai as genai
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import uuid, socket, os, json, random, string, time, PyPDF2
from datetime import datetime, timedelta

# 1. LOAD CONFIG
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'default_dev_key_123456789')

# 2. CẤU HÌNH AI
api_key = os.getenv('GOOGLE_API_KEY')
if not api_key:
    print("⚠️ CẢNH BÁO: Chưa tìm thấy GOOGLE_API_KEY. Hãy kiểm tra file .env")
else:
    genai.configure(api_key=api_key)

def get_ai_model():
    try:
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        if 'models/gemini-1.5-flash' in models: return genai.GenerativeModel('gemini-1.5-flash')
        if 'models/gemini-2.0-flash-exp' in models: return genai.GenerativeModel('gemini-2.0-flash-exp')
        return genai.GenerativeModel('gemini-1.5-pro')
    except: return genai.GenerativeModel('gemini-1.5-flash') 

model = get_ai_model()

# 3. DATABASE & UPLOAD
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URI', 'sqlite:///talentflow.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ================= DATABASE MODELS =================

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='recruiter')
    full_name = db.Column(db.String(100))

class Interview(db.Model):
    id = db.Column(db.String(4), primary_key=True)
    recruiter_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    field = db.Column(db.String(100), nullable=False)
    base_questions = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.String(20))

class Candidate(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    room_id = db.Column(db.String(4), db.ForeignKey('interview.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), nullable=False)
    cv_filename = db.Column(db.String(200), nullable=True)
    personal_questions = db.Column(db.Text, nullable=True) 
    videos = db.relationship('Video', backref='candidate', lazy=True)

class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    candidate_id = db.Column(db.String(36), db.ForeignKey('candidate.id'), nullable=False)
    question_index = db.Column(db.Integer, nullable=False)
    filename = db.Column(db.String(200), nullable=False)
    ai_score = db.Column(db.Float, default=0.0)
    ai_summary = db.Column(db.Text, default="")

# ================= AI LOGIC =================

def extract_text_from_pdf(pdf_path):
    try:
        text = ""
        with open(pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages: text += page.extract_text()
        return text[:5000]
    except: return ""

def clean_json_text(text):
    try:
        text = text.replace('```json', '').replace('```', '').strip()
        if '[' in text and ']' in text:
            start = text.find('[')
            end = text.rfind(']') + 1
            return json.loads(text[start:end])
        elif '{' in text and '}' in text:
            start = text.find('{')
            end = text.rfind('}') + 1
            return json.loads(text[start:end])
        return json.loads(text)
    except Exception as e:
        print(f"JSON Parse Error: {e}")
        return None

def ai_generate_questions(job_title, count=5):
    try:
        prompt = f"""
        Act as a Senior Recruiter. Create exactly {count} professional interview questions for the role: "{job_title}".
        Output format: A raw JSON array of strings. 
        Example: ["Question 1", "Question 2", "Question 3"]
        Do not output markdown or any other text.
        """
        response = model.generate_content(prompt)
        qs = clean_json_text(response.text)
        if isinstance(qs, list) and len(qs) > 0: return qs[:count]
        else: raise ValueError("Invalid JSON")
    except:
        return ["Why are you interested in this position?", "Describe a challenge you overcame.", "What are your key strengths?", "How do you handle deadlines?", "Where do you see yourself in 3 years?"][:count]

def ai_generate_cv_questions(cv_text, job_title):
    try:
        prompt = f"""
        Role: {job_title}. CV excerpt: "{cv_text[:2000]}..."
        Generate 2 interview questions specifically based on this CV content.
        Output format: A raw JSON array of strings.
        """
        response = model.generate_content(prompt)
        qs = clean_json_text(response.text)
        return qs[:2] if qs else ["Tell me about a project in your CV.", "What is your best skill?"]
    except: return ["Tell me about a project in your CV.", "What is your best skill?"]

def ai_grade_single_video(video_path, question_text):
    if not os.path.exists(video_path) or os.path.getsize(video_path) < 1000:
        return {"score": 0, "summary": "Error: Video corrupted."}
    try:
        video_file = genai.upload_file(path=video_path, mime_type="video/webm")
        while video_file.state.name == "PROCESSING":
            time.sleep(2)
            video_file = genai.get_file(video_file.name)
            
        if video_file.state.name == "FAILED": raise ValueError("Video processing failed.")

        prompt = f"""
        Evaluate answer for: "{question_text}".
        Rules:
        1. If silent/no audio -> Score: 0, Summary: "No answer detected."
        2. Otherwise -> Score (0-10), Summary: Key points.
        Output JSON: {{ "summary": "...", "score": 8.5 }}
        """
        response = model.generate_content([video_file, prompt], generation_config={"response_mime_type": "application/json"})
        try: genai.delete_file(video_file.name)
        except: pass
        return json.loads(response.text)
    except Exception as e:
        return {"score": 0, "summary": f"AI Error: {str(e)}"}

# ================= ROUTES =================

@app.route('/')
def home(): return render_template('home.html')

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

@app.route('/dashboard', methods=['GET', 'POST'])
def recruiter_dashboard():
    if session.get('role') != 'recruiter': return redirect(url_for('login'))
    if request.method == 'POST':
        field = request.form.get('field')
        try: count = int(request.form.get('count', 5))
        except: count = 5
        qs = ai_generate_questions(field, count)
        rid = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        db.session.add(Interview(id=rid, recruiter_id=session['user_id'], field=field, base_questions=json.dumps(qs), created_at=datetime.now().strftime("%Y-%m-%d %H:%M")))
        db.session.commit()
        return redirect(url_for('recruiter_dashboard'))

    my_interviews = Interview.query.filter_by(recruiter_id=session['user_id']).order_by(Interview.created_at.desc()).all()
    dashboard_data = []
    for i in my_interviews:
        cands = Candidate.query.filter_by(room_id=i.id).all()
        cand_list = [{"id": c.id, "name": c.name, "email": c.email, "count": Video.query.filter_by(candidate_id=c.id).count()} for c in cands]
        dashboard_data.append({"id": i.id, "field": i.field, "date": i.created_at, "question_count": len(json.loads(i.base_questions)), "candidates": cand_list})
    return render_template('recruiter_dashboard.html', data=dashboard_data, recruiter_name=session.get('name'))

@app.route('/report/<cid>')
def view_report(cid):
    if not session.get('user_id'): return redirect(url_for('login'))
    
    # --- FIX: Cập nhật cú pháp mới db.session.get() ---
    cand = db.session.get(Candidate, cid)
    if not cand: return "Not Found"
    
    room = db.session.get(Interview, cand.room_id)
    if session.get('role') == 'recruiter' and room.recruiter_id != session['user_id']: return "Unauthorized Access"

    qs = json.loads(cand.personal_questions)
    videos = Video.query.filter_by(candidate_id=cand.id).all()
    results, total = {}, 0
    
    for v in videos:
        if v.ai_score == 0 and not v.ai_summary:
            ai = ai_grade_single_video(os.path.join(app.config['UPLOAD_FOLDER'], v.filename), qs[v.question_index])
            v.ai_score, v.ai_summary = ai.get('score', 0), ai.get('summary', '')
            db.session.commit()
        results[str(v.question_index)] = {"score": v.ai_score, "summary": v.ai_summary, "file": v.filename}
        total += v.ai_score
        
    return render_template('report.html', candidate=cand, room=room, results=results, total=round(total,1), max=len(qs)*10, questions=qs)

@app.route('/candidate', methods=['GET', 'POST'])
def candidate_portal():
    if request.method == 'POST':
        rid = request.form.get('room_id').upper().strip()
        name = request.form.get('name').strip()
        email = request.form.get('email').strip().lower()

        # --- FIX: Cập nhật cú pháp mới db.session.get() ---
        room = db.session.get(Interview, rid)
        if not room: return render_template('candidate_portal.html', error="Invalid Room Code")
        
        existing_cand = Candidate.query.filter_by(room_id=rid, email=email).first()
        if existing_cand:
            if existing_cand.name.lower() == name.lower():
                session['cid'] = existing_cand.id
                return redirect(url_for('candidate_review'))
            else:
                return render_template('candidate_portal.html', error="This email is already registered with a different name!")
        
        cv_file = request.files.get('cv_file')
        cv_name, cv_text = None, ""
        if cv_file and cv_file.filename != '':
            cv_name = secure_filename(f"{uuid.uuid4()}_{cv_file.filename}")
            path = os.path.join(app.config['UPLOAD_FOLDER'], cv_name)
            cv_file.save(path)
            cv_text = extract_text_from_pdf(path)
            
        qs = json.loads(room.base_questions)
        if cv_text: qs.extend(ai_generate_cv_questions(cv_text, room.field))
        
        cid = str(uuid.uuid4())
        session['cid'] = cid
        db.session.add(Candidate(id=cid, room_id=rid, name=name, email=email, cv_filename=cv_name, personal_questions=json.dumps(qs)))
        db.session.commit()
        return redirect(url_for('interview_room'))
        
    return render_template('candidate_portal.html')

@app.route('/interview')
def interview_room():
    if not session.get('cid'): return redirect(url_for('candidate_portal'))
    # --- FIX: Cập nhật cú pháp mới db.session.get() ---
    cand = db.session.get(Candidate, session.get('cid'))
    return render_template('interview.html', questions=json.loads(cand.personal_questions))

@app.route('/candidate/review')
def candidate_review():
    if not session.get('cid'): return redirect(url_for('home'))
    # --- FIX: Cập nhật cú pháp mới db.session.get() ---
    cand = db.session.get(Candidate, session.get('cid'))
    videos = Video.query.filter_by(candidate_id=cand.id).all()
    v_dict = {str(v.question_index): v.filename for v in videos}
    return render_template('candidate_review.html', candidate=cand, questions=json.loads(cand.personal_questions), videos=v_dict)

@app.route('/upload_video', methods=['POST'])
def upload_video():
    cid = session.get('cid')
    if not cid: return jsonify({"status": "error"}), 400
    
    file = request.files['video']
    idx = request.form.get('question_index') # Giá trị này từ client gửi lên là 0, 1, 2...
    
    if file:
        cand = db.session.get(Candidate, cid)
        
        if cand:
            safe_name = secure_filename(cand.name)
            # --- SỬA Ở ĐÂY: Cộng thêm 1 để tên file bắt đầu từ 1 ---
            file_idx = int(idx) + 1 
            fname = f"{safe_name}_{cid[:4]}_q{file_idx}.webm"
        else:
            file_idx = int(idx) + 1
            fname = secure_filename(f"{cid}_q{file_idx}.webm")

        file.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
        
        # Lưu vào Database vẫn dùng 'idx' gốc (0, 1, 2) để khớp với danh sách câu hỏi trong code
        exist = Video.query.filter_by(candidate_id=cid, question_index=int(idx)).first()
        
        if exist:
            exist.filename = fname
            exist.ai_score = 0
            exist.ai_summary = ""
        else:
            # Lưu ý: question_index trong DB vẫn để int(idx) (là 0)
            # Nếu sửa thành file_idx (là 1) thì lúc lấy câu hỏi số 0 ra chấm điểm sẽ bị lệch.
            db.session.add(Video(candidate_id=cid, question_index=int(idx), filename=fname))
            
        db.session.commit()
        return jsonify({"status": "success"})
        
    return jsonify({"status": "error"}), 500
        

@app.route('/uploads/<filename>')
def uploaded_file(filename): return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('home'))

def init_db():
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(role='manager').first():
            db.session.add(User(username='manager', password_hash=generate_password_hash('admin123'), full_name='System Admin', role='manager'))
            db.session.commit()
            print("✅ Manager Account Ready")

if __name__ == '__main__': init_db(); app.run(host='0.0.0.0', port=5000, debug=True)