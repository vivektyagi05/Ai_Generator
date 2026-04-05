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

* Resend API integration
* Async email sending
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

---

## 👨‍💻 Author

**Vivek Kumar**
B.Tech Computer Science Student
Developer

---

## ⭐ Support

If you like this project, give it a ⭐ on GitHub and connect with me on LinkedIn.



### 🔐 OTP & Email Verification (Important Notice)

The OTP-based email verification system in this project is currently **partially disabled for public users**.

#### ❗ Reason

This project uses an external email service (**Resend API**) for sending OTP emails.
In the free/testing mode, the service only allows sending emails to the developer’s own verified email address.

#### ⚠️ Current Limitation

* New users **cannot receive OTP emails** on signup
* Email-based features like:

  * Signup verification
  * Password reset
  * OTP resend
    are **not fully functional for public users**

#### ✅ What Works

* UI / Frontend flow
* OTP generation logic
* Backend integration
* All other features of the project are working correctly

#### 🚀 Future Plan

The OTP system will be fully enabled after upgrading to a paid plan and verifying a custom domain.

---

💡 *This limitation is only due to email service restrictions — not a bug in the code.*
