import os 
import pytz
import json
import requests
import faiss
import re
import sqlite3
from functools import wraps
import requests
from sqlalchemy.orm import joinedload
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, session, g, send_from_directory, current_app
from flask import current_app
import secrets
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import uuid
from datetime import datetime
from werkzeug.utils import secure_filename
from . import classify_media
from .database import db, User ,Post, Media  , Notification
from .utils import classify_media
import numpy as np
import pickle
from flask import abort



main = Blueprint("main", __name__)

# Load .env
load_dotenv()

main = Blueprint('main', __name__)



def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('user_id'):
            lang = request.args.get('lang', 'ar')
            flash('Please log in first.' if lang=='en' else 'الرجاء تسجيل الدخول أولاً.', 'warning')
            return redirect(url_for('main.login', lang=lang))
        return view(*args, **kwargs)
    return wrapped



OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") 
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions" 
DEFAULT_MODEL = "gpt-3.5-turbo" 
if not OPENAI_API_KEY:
    print("⚠ Warning: OPENAI_API_KEY not set — chatbot will not work until it's set.")



def call_openai(payload):
    if not OPENAI_API_KEY:
        return {"error": "Server: OPENAI_API_KEY not configured."}

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            OPENAI_API_URL,
            headers=headers,
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print("❌ OpenAI request failed:", e)
        return {"error": str(e)}




# ==== Password & Phone Validation ====
PASSWORD_REGEX = re.compile(r'^(?=.*[A-Z])(?=.*\d).{6,}$')
PHONE_REGEX = re.compile(r'^\+?\d{8,15}$')

def valid_password(pw):
    return bool(PASSWORD_REGEX.match(pw))

def valid_phone(phone):
    return bool(PHONE_REGEX.match(phone))


# --- Pages ------------------------------------------------
@main.route('/')
@main.route('/home')
def home():
    lang = request.args.get('lang', 'ar')
    return render_template('home.html', lang=lang)


@main.route('/home_fully')
def home_fully():
    lang = request.args.get('lang', 'ar')
    return render_template('home_fully.html', lang=lang)


@main.route('/about')
def about():
    lang = request.args.get('lang', 'ar')
    return render_template('about.html', lang=lang)

@main.route('/guidebot')
def guidebot():
    lang = request.args.get('lang', 'ar')
    return render_template('guidebot.html', lang=lang)

# ==== Register ====
@main.route('/register', methods=['GET', 'POST'])
def register():
    lang = request.args.get('lang', 'ar')

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        email = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''

        errors = []
        if not username:
            errors.append("Username is required." if lang == 'en' else "اسم المستخدم مطلوب.")
        if not email:
            errors.append("Email is required." if lang == 'en' else "البريد الإلكتروني مطلوب.")
        if not password:
            errors.append("Password is required." if lang == 'en' else "كلمة المرور مطلوبة.")
        if password and not valid_password(password):
            errors.append(
                "Password must be at least 6 characters, include 1 uppercase and 1 number."
                if lang == 'en' else
                "كلمة المرور يجب أن تكون 6 أحرف على الأقل وتحتوي على حرف كبير واحد ورقم واحد."
            )

        # Check duplicates
        if User.query.filter_by(username=username).first():
            errors.append("Username already exists." if lang == 'en' else "اسم المستخدم موجود بالفعل.")
        if User.query.filter_by(email=email).first():
            errors.append("Email already exists." if lang == 'en' else "البريد الإلكتروني موجود بالفعل.")

        # If errors, reload register form
        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('register.html', lang=lang, form=request.form)

        # Save new user
        pw_hash = generate_password_hash(password)
        user = User(username=username, email=email, password=pw_hash, is_guest=False)
        db.session.add(user)
        db.session.commit()

        # ✅ Auto login after register
        session['user_id'] = user.id
        session['username'] = user.username
        session['is_guest'] = False

        flash("Registration successful." if lang == 'en' else "تم إنشاء الحساب بنجاح.", 'success')
        return redirect(url_for('main.home_fully', lang=lang))

    return render_template('register.html', lang=lang)



# ==== Login ====
@main.route('/login', methods=['GET','POST'])
def login():
    lang = request.args.get('lang', 'ar')
    if request.method == 'POST':
        username_or_email = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''

        if not username_or_email or not password:
            flash("Username/Email and password are required." if lang=='en' else "اسم المستخدم/البريد الإلكتروني وكلمة المرور مطلوبان.", 'danger')
            return render_template('login.html', lang=lang, form=request.form)

        # ✅ Find user by username OR email
        user = User.query.filter(
            (User.username == username_or_email) | (User.email == username_or_email)
        ).first()

        if not user or not check_password_hash(user.password, password):
            flash("Invalid username/email or password." if lang=='en' else "اسم المستخدم/البريد الإلكتروني أو كلمة المرور غير صحيح.", 'danger')
            return render_template('login.html', lang=lang, form=request.form)

        # ✅ Store session data including is_guest
        session['user_id'] = user.id
        session['username'] = user.username
        session['is_guest'] = user.is_guest

        flash(
            f"Welcome, {user.username}!" if lang=='en' else f"مرحباً، {user.username}!",
            'success'
        )
        return redirect(url_for('main.home_fully', lang=lang))

    return render_template('login.html', lang=lang)



@main.route('/logout')
def logout():
    lang = request.args.get('lang', 'ar')
    session.clear()
    flash("You have been logged out." if lang=='en' else "تم تسجيل الخروج.", 'info')
    return redirect(url_for('main.home', lang=lang))
 


@main.route('/guest-start')
def guest_start():
    lang = request.args.get('lang', 'ar')

    # Create a display name like Guest-7fa3
    username = f"Guest-{secrets.token_hex(2)}"

    # Random password (unused) just to satisfy NOT NULL
    random_pw = generate_password_hash(secrets.token_urlsafe(16))

    user = User(
        username=username,
        email=None,
        password=random_pw,
        is_guest=True
    )
    db.session.add(user)
    db.session.commit()

    # Session flags
    session['user_id'] = user.id
    session['username'] = username
    session['is_guest'] = True

    return redirect(url_for('main.home_fully', lang=lang))



@main.route('/post', methods=['GET', 'POST'])
def post():
    lang = request.args.get('lang', 'ar')

    if request.method == 'POST':
        # Ensure user is logged in
        user_id = session.get('user_id')
        if not user_id:
            flash("You must be logged in to post.", "danger")
            return redirect(url_for('main.login', lang=lang))

        # Collect form data
        age = request.form.get('age')
        gender = request.form.get('gender')
        state = request.form.get('state')
        locality = request.form.get('locality')
        misinfo = request.form.get('misinfo')
        followup = request.form.get('followup')
        decision = request.form.get('decision')
        danger = request.form.get('danger')
        content = request.form.get('story')
        time = request.form.get('time') 

        # Validate mandatory fields
        if not content or not misinfo or not danger:
            flash("Please fill all required fields.", "danger")
            return redirect(url_for('main.post', lang=lang))

        # Update user profile with Age & Gender (if not already stored)
        user = User.query.get(user_id)
        if user:
            if age: 
                user.age = age
            if gender: 
                user.gender = gender

        # Create new Post
        new_post = Post(
            content=content,
            user_id=user_id,
            state=state,
            locality=locality,
            misinfo_type=misinfo,
            followup=followup,
            decision=(decision == "True"),
            danger_level=danger,
            created_at=datetime.utcnow(),
            time=time
        )
        db.session.add(new_post)
        db.session.flush()  # flush so new_post.id is available

        # Handle uploaded media (if any)
        files = request.files.getlist('media')
        upload_folder = os.path.join('app', 'uploads')
        os.makedirs(upload_folder, exist_ok=True)

        for file in files:
            if file and file.filename:
                filename = secure_filename(file.filename)
                filepath = os.path.join(upload_folder, filename)
                file.save(filepath)

                media_item = Media(
                    filename=filename,
                    media_type=file.mimetype.split('/')[0],  
                    post_id=new_post.id
                )
                db.session.add(media_item)
        
        other_users = User.query.filter(User.id != user_id, User.is_guest == False).all()
        for u in other_users:
           notif = Notification(
           user_id=u.id,
           message=f"{user.username} has posted a new story."
      )
           db.session.add(notif)
        
        db.session.commit()

        flash("Your post has been shared successfully!", "success")
        return redirect(url_for('main.posts_list', lang=lang))

    return render_template('post.html', lang=lang)








@main.app_context_processor
def inject_notifications():
    user_id = session.get("user_id")
    notifs = []
    unread_count = 0

    if user_id:
        notifs = Notification.query.filter_by(user_id=user_id).order_by(Notification.created_at.desc()).limit(5).all()
        unread_count = Notification.query.filter_by(user_id=user_id, is_read=False).count()

    return dict(notifications=notifs, unread_count=unread_count)


@main.route("/notifications/read_all")
@login_required
def read_all_notifications():
    user_id = session.get("user_id")
    Notification.query.filter_by(user_id=user_id, is_read=False).update({"is_read": True})
    db.session.commit()
    return redirect(request.referrer or url_for("main.home"))


# ==== Serve uploaded files ====
@main.route('/uploads/<filename>')
def uploaded_file(filename):
    upload_folder = os.path.join(current_app.root_path, 'uploads')
    return send_from_directory(upload_folder, filename)





# ==== Posts ====
@main.route('/posts', methods=['GET'])
def posts_list():
    lang = request.args.get('lang', 'ar')

    filter_by = request.args.get('filter')
    value = request.args.get('value')

    # Start with all posts
    query = Post.query.options(
        joinedload(Post.user),
        joinedload(Post.media_items)
    )

    # Apply filtering
    if filter_by == "type":
        # filter by main type of misinformation
        query = query.filter(Post.misinfo_type == value)
    elif filter_by == "followup":
        # filter by sub-option (followup)
        query = query.filter(Post.followup == value)
    elif filter_by == "danger":
        query = query.filter(Post.danger_level == value)
    elif filter_by == "state":
        query = query.filter(Post.state == value)
    elif filter_by == "owner":
        if value == "me" and session.get("user_id"):
            query = query.filter(Post.user_id == session["user_id"])

    posts = query.order_by(Post.created_at.desc(), Post.id.desc()).all()

    return render_template('posts_list.html', lang=lang, posts=posts)







# === Edit Post ===
@main.route('/post/<int:post_id>/edit', methods=['GET', 'POST'])
def edit_post(post_id):
    post = Post.query.get_or_404(post_id)

    # Only the owner can edit
    if post.user_id != session.get('user_id'):
        abort(403)

    if request.method == 'POST':
        post.content = request.form.get('story')
        

        db.session.commit()
        flash("✅ Your post was updated successfully!", "success")
        return redirect(url_for('main.posts_list'))

    return render_template('edit_post.html', post=post)


# === Delete Post ===
@main.route('/post/<int:post_id>/delete', methods=['POST'])
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)

    # Only the owner can delete
    if post.user_id != session.get('user_id'):
        abort(403)

    db.session.delete(post)
    db.session.commit()
    flash("🗑 Your post was deleted successfully.", "success")
    return redirect(url_for('main.posts_list'))



# ==== Chatbot Helpers ====


# --- Load FAISS index + metadata ---

INDEX_PATH = "knowledge.index"
META_PATH = "metadata.jsonl"

faiss_index = None
metadata = []

def load_kb():
    global faiss_index, metadata
    if os.path.exists(INDEX_PATH):
        faiss_index = faiss.read_index(INDEX_PATH)
        print(f"✅ Loaded FAISS index with {faiss_index.ntotal} vectors")
    else:
        print("⚠ No FAISS index found")

    if os.path.exists(META_PATH):
        with open(META_PATH, "r", encoding="utf-8") as f:
            metadata = [json.loads(line) for line in f]
        print(f"✅ Loaded metadata with {len(metadata)} chunks")
    else:
        print("⚠ No metadata.jsonl found")

load_kb()


# ==== Helper: Embed & Retrieve ====
def retrieve_context(query, top_k=3):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or faiss_index is None:
        return []

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    resp = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers=headers,
        json={"model": "text-embedding-3-small", "input": query},
        timeout=30,
    )
    resp.raise_for_status()
    emb = resp.json()["data"][0]["embedding"]
    vec = np.array([emb], dtype="float32")

    D, I = faiss_index.search(vec, top_k)
    chunks = []
    for idx in I[0]:
        if idx < len(metadata):
            chunks.append(metadata[idx]["text"])
    return chunks


# ==== Chat Route ====
@main.route("/api/chat", methods=["POST"])
@login_required
def chat():
    data = request.json
    message = data.get("message", "").strip()
    lang = data.get("lang", "en")

    if not message:
        return jsonify({"reply": "⚠ Please enter a message."})

    # --- Step 1: Greeting & casual talk detection ---
    GREETINGS = ["hi", "hello", "hey", "salam", "good morning", "good evening", "thanks", "thank you"]
    if any(word in message.lower() for word in GREETINGS):
        reply = "👋 Hello! How can I assist you today about Sudan’s situation?"
        return jsonify({"reply": reply})

    # --- Step 2: Detect request for latest news ---
    NEWS_KEYWORDS = ["latest news", "last updates", "recent news", "what is new", "current news", "updates"]
    if any(kw in message.lower() for kw in NEWS_KEYWORDS):
        context_chunks = retrieve_context("latest news in Sudan war", top_k=5)
        context_text = "\n\n".join(context_chunks)

        if not context_chunks:
            reply = "⚠ Sorry, I don’t have recent updates in the trusted sources right now."
            return jsonify({"reply": reply})

        system_prompt = (
            "You are GuideBot, an assistant that summarizes the most recent verified updates about Sudan’s war.\n\n"
            "Rules:\n"
            "- Summarize the conflict situation clearly and concisely.\n"
            "- If context exists, use it to provide a short update.\n"
            "- Be factual and avoid speculation.\n\n"
            f"Trusted knowledge base:\n{context_text}"
        )

        api_key = os.getenv("OPENAI_API_KEY")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message}
            ],
            "temperature": 0.3,
            "max_tokens": 300,
        }

        try:
            resp = requests.post("https://api.openai.com/v1/chat/completions",
                                 headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            reply = resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print("❌ OpenAI error:", e)
            reply = "⚠ Sorry, I couldn’t fetch the latest updates now."

        return jsonify({"reply": reply, "sources": context_chunks})

    # --- Step 3: Standard misinformation verification ---
    context_chunks = retrieve_context(message, top_k=3)
    context_text = "\n\n".join(context_chunks)

    system_prompt = (
        "You are GuideBot, a friendly misinformation verification assistant for Sudan’s war.\n\n"
        "Rules:\n"
        "- Always reply in a polite, human-like tone.\n"
        "- Use the trusted knowledge base below when possible.\n"
        "- If the context does not confirm the claim, DO NOT just say 'not verified'. "
        "Instead, say something like: 'I couldn’t verify that specific claim, but here’s what is known about Sudan’s conflict.'\n"
        "- Always stay factual, short, and clear.\n\n"
        f"Trusted knowledge base:\n{context_text if context_chunks else 'No specific sources available.'}"
    )

    api_key = os.getenv("OPENAI_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message}
        ],
        "temperature": 0.3,
        "max_tokens": 300,
    }

    try:
        resp = requests.post("https://api.openai.com/v1/chat/completions",
                             headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        reply = resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print("❌ OpenAI error:", e)
        reply = "⚠ Sorry, the verification service is not available."

    return jsonify({"reply": reply, "sources": context_chunks})


