# Overview


TalentFlow AI is a web app for video interviews recorded per question, with three roles:
* Manager: creates Recruiter accounts.
* Recruiter: creates interview rooms (room codes) by job/position and reviews candidate results.
* Candidate: signs in with email (one email per account), uploads a CV (PDF), then the system’s AI generates questions (based on the position and the uploaded CV). The candidate records an answer for each question.


Tech used:
* Backend: Flask, Flask-SQLAlchemy
* Default DB: SQLite (talentflow.db)
* AI: Google Generative AI (Gemini) to generate questions and score/summarize each video
* File storage (videos/CVs): uploads/ (auto-created)


Main Features
* Manager logs in and creates recruiter (judge) accounts.
* Recruiter creates a room code (4 alphanumeric chars) per position and seeds a base question set.
* Candidate joins with room code, signs in with email (one email = one account in that room), uploads CV → AI adds 2 personalized questions.
* Per-question recording in the interview page; each answer is uploaded immediately to the server.
* Recruiter opens a report for each candidate: AI score (0–10), a short summary per answer, and links to video files.


Quick Setup
* Requirements: Python 3.10+
* Install dependencies (run from project root):  pip install -r requirements.txt
* Create a .env file (same folder as app.py):
* SECRET_KEY=put_a_new_secret_here
* GOOGLE_API_KEY= <Get it here: https://aistudio.google.com/app/apikey >
* DATABASE_URI=sqlite:///talentflow.db

## Notes:
If DATABASE_URI is omitted, SQLite is used by default.
If you don’t have GOOGLE_API_KEY, the app still runs and AI falls back to default questions/scores.


Run the app (development):
python app.py


The app runs at:
http://127.0.0.1:5000/

On first run, the system seeds a Manager account:
* username: manager
* password: admin123


## Roles & User Flows

Manager
* Visit /login and sign in with the manager account.
* Admin page: /manager
* Create recruiter accounts (username, password, full name).
* View existing recruiters.

Recruiter
* Sign in at /login (with a recruiter account).
* Dashboard: /dashboard
* Create a room for a position (field) and number of base questions (default 5).
→ The system uses AI to generate base questions and a 4-character room code (e.g., AB12).
* View the list of candidates who joined that room and how many videos each submitted.
* Click a candidate to open the report: /report/<candidate_id>
* On first open, the system calls AI to score and summarize each video; results are cached in the DB.


Candidate
* Candidate portal: /candidate
* Enter room code, full name, email (each email can register one account within the same room).
* Upload CV (PDF) → the system extracts text → AI generates 2 personalized questions and merges them with the base set.
* Go to the interview room: /interview
* Record answers one by one; each submission uploads that question’s video to the server.
* Review page: /candidate/review (see your own submitted videos if any).


## Data Structures & Storage

DB Model (simplified)
* User(id, username, password_hash, role, full_name)
* role: manager | recruiter
* Interview(id, recruiter_id, field, base_questions, created_at)
* id: 4-character room code
* base_questions: JSON list of questions
* Candidate(id, room_id, name, email, cv_filename, personal_questions)
* personal_questions: JSON list (base + personalized)
* Video(id, candidate_id, question_index, filename, ai_score, ai_summary)


Files
*Folder: uploads/ (auto-created)
*Video filename: {candidate_uuid}_q{index}.webm
*CV filename: {uuid}_{original}.pdf
*Access files at: /uploads/


## Main Endpoints/Routes

* GET / Home (template home.html)
* GET/POST /login Sign in (manager/recruiter)
* GET/POST /manager Manager admin (create recruiters)
* GET/POST /dashboard Recruiter dashboard (create room, view list)
* GET /report/ Candidate report (AI scoring if not done yet)
* GET/POST /candidate Candidate entry (room, email, upload CV)
* GET /interview Candidate interview page (list questions)
* GET /candidate/review Candidate’s review of uploaded videos
* POST /upload_video Upload one video per question
* Fields: video (WebM file), question_index (int)
* Uses session cid to assign the video to the correct candidate
* GET /uploads/ Serve static files (video/CV)
* GET /logout Sign out (clear session)


## AI Integration

* Generate base questions by position: call Gemini and request a JSON array of questions (fallback if API fails).
* Add 2 personalized questions from extracted CV text (PDF).
* Score and summarize videos: upload .webm to Gemini for analysis; return JSON {score: 0..10, summary: "..."}.

* If API key is missing or errors occur, the system still works using fallback questions/scores.


## Security & Privacy

* Flask session secret from SECRET_KEY (.env).
* Manager/Recruiter passwords hashed via werkzeug.security.
* No separate face images stored; only videos and CVs users upload are saved.


## Production recommendations:
* Enforce HTTPS (secure camera/mic access).
* Add size limits and MIME allowlist at the app or reverse proxy.
* Strengthen auth/authorization for static routes if needed.
* Change the default manager/admin123 password immediately.


## Quick Test

* Manager login /login → /manager creates a new recruiter.
* Recruiter login → /dashboard creates a new room for “Software Engineer”, gets a room code (e.g., 9K2A) and the base questions.
 Candidate at /candidate:
* Enter valid room code, full name, new email.
* Upload CV (PDF) → /interview shows the questions (base + 2 personalized).
* Record question 1 → submit → check uploads/ for {cid}_q1.webm.
* Continue with the remaining questions.
 Recruiter opens /report/:
* First visit: system calls AI to score/summary each video; results appear.
* Reload: scores/summaries are already stored in the DB.


## Common Errors & Handling

* No GOOGLE_API_KEY: the app warns in logs; AI falls back.
* Upload failure: check write permissions for uploads/, video format .webm, and file size limits.
* “Invalid Room Code”: verify the 4-character code created at /dashboard.
* “This email is already registered with a different name!”: within the same room, one email is bound to a single name; change email or use the same name.


## Future Development/ Suggestions

* Add “Finish session” to lock submissions after completion.
* Frontend retry/backoff during upload and resume state after refresh.
* Configurable max number of questions and per-question time limit.
* Store videos on S3 (with presigned URLs) instead of filesystem.
* Add transcript (STT) → compute WPM/filler → “Delivery Coach” tips.


This document is written based on the project’s app.py (Flask, SQLAlchemy, Google Generative AI).
