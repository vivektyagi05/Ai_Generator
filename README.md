# 🤖 AI Generator – Intelligent AI Web Platform

A production-ready AI-powered web application built using **Django**, designed to generate intelligent responses using GROQ AI APIs with secure authentication, OTP verification, and a modern user experience.

---

## 🚀 Live Demo

👉 [https://ai-generator-k573.onrender.com](https://ai-generator-k573.onrender.com)

---

## ✨ Key Features

### 🔐 Authentication System

* Secure user signup & login
* Email OTP verification
* Password reset with OTP
* Session-based authentication

### 🤖 AI Integration

* GROQ AI (LLaMA 3.1) powered responses
* Fast API-based intelligent chat
* Retry & timeout handling for reliability

### 📧 Production Email System

* Brevo transactional email API integration
* Retry with exponential backoff for transient failures
* OTP verification for signup & password reset

### 🧑‍💻 User Dashboard

* Chat history tracking
* Profile management
* Avatar upload support
* Password change & account deletion

### ⚡ Performance & Security

* Environment-based configuration (.env)
* Production-ready Django settings
* Secure cookies & CSRF protection
* Gunicorn deployment on Render

---

## 🛠️ Tech Stack

**Backend**

* Python
* Django 3.2
* Django Authentication System

**AI**

* GROQ API (LLaMA 3.1 Model)


## 📂 Project Structure

```
AI_GENERATORS/
│
├── accounts/           # Authentication & Profile
├── templates/          # HTML Templates
├── staticfiles/        # Static Assets
├── manage.py
└── requirements.txt
```




## 📈 Future Enhancements

* PostgreSQL database integration
* Payment-based premium AI access
* Real-time streaming responses
* Advanced analytics dashboard
* Credit/entitlement system
---

## 👨‍💻 Author

**Vivek Kumar**
B.Tech Computer Science Student
Developer

---

## ⭐ Support

If you like this project, give it a ⭐ on GitHub and connect with me on LinkedIn.



### 🔐 OTP & Email Verification

This project sends OTP/transactional email via the **Brevo** transactional
email API (see `EMAIL_SETUP.md` for full setup). Brevo requires verifying a
**sender email address** (not a full DNS domain) before it will deliver to
real recipients — set `BREVO_API_KEY`, `EMAIL_FROM`, and `EMAIL_FROM_NAME` in
your environment once that's done. Until a sender is verified, OTP emails
will fail to send and signup/password-reset will surface a clear
"couldn't send verification code" error rather than silently failing.

---

💡 *See `EMAIL_SETUP.md` for the full Brevo setup walkthrough, rate-limiting
behavior, and common error troubleshooting.*
