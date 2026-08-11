"""
Template Renderer — All HTML email templates for AI Generators.

Templates are pure functions: input in, HTML string out. They know
nothing about providers, retries, or transport. This separation is what
makes adding a new email type just a matter of adding one function here
plus one thin wrapper in EmailService.

The previous implementation sent plain-text emails (e.g. "Your OTP is
123456") via Django's send_mail. These templates upgrade that to branded
HTML while keeping the same information content.
"""

from accounts.email.exceptions import TemplateRenderError

_FOOTER_YEAR = "2026"


def _shell(inner_html: str) -> str:
    """Shared outer HTML shell (header, card, footer) used by every email."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI Generators</title>
</head>
<body style="margin:0;padding:0;background:#0b0f19;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0b0f19;padding:40px 20px;">
    <tr>
      <td align="center">
        <table width="560" cellpadding="0" cellspacing="0"
               style="background:#111827;border-radius:16px;border:1px solid rgba(255,255,255,0.08);overflow:hidden;">

          <!-- Header -->
          <tr>
            <td align="center"
                style="padding:32px 40px 24px;background:linear-gradient(135deg,#6d28d9,#2563eb);">
              <table cellpadding="0" cellspacing="0">
                <tr>
                  <td style="background:rgba(255,255,255,0.15);border-radius:12px;padding:10px 14px;
                              vertical-align:middle;">
                    <span style="font-size:22px;">✨</span>
                  </td>
                  <td style="padding-left:12px;">
                    <span style="color:#fff;font-size:22px;font-weight:700;letter-spacing:-0.5px;">
                      AI Generators
                    </span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:40px 40px 32px;">
              {inner_html}
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding:20px 40px;background:#0d1220;border-top:1px solid rgba(255,255,255,0.05);">
              <p style="color:#475569;font-size:12px;margin:0;text-align:center;">
                © {_FOOTER_YEAR} Ai_Generator.com · AI Tools Suite
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def render_otp_email(otp_code: str, heading: str, sub_heading: str) -> str:
    """Render an OTP email — used for both signup and password-reset codes."""
    try:
        inner = f"""<h2 style="color:#e5e7eb;font-size:22px;font-weight:600;margin:0 0 8px;">{heading}</h2>
              <p style="color:#94a3b8;font-size:15px;margin:0 0 32px;line-height:1.6;">{sub_heading}</p>

              <!-- OTP Box -->
              <div style="background:#1e1b4b;border:1px solid #6d28d9;border-radius:12px;
                          padding:28px;text-align:center;margin-bottom:28px;">
                <p style="color:#94a3b8;font-size:13px;margin:0 0 12px;text-transform:uppercase;
                           letter-spacing:2px;font-weight:600;">Your Verification Code</p>
                <span style="color:#fff;font-size:42px;font-weight:800;letter-spacing:16px;
                             font-family:'Courier New',monospace;">{otp_code}</span>
                <p style="color:#818cf8;font-size:13px;margin:16px 0 0;font-weight:500;">
                  ⏱ Expires in 5 minutes
                </p>
              </div>

              <p style="color:#64748b;font-size:13px;line-height:1.7;margin:0;">
                If you did not request this code, you can safely ignore this email.
                Your account is secure and no action is required.
              </p>"""
        return _shell(inner)
    except Exception as exc:
        raise TemplateRenderError("Failed to render OTP email template.") from exc


def render_welcome_email(name: str, email: str) -> str:
    """Render the welcome email sent right after successful signup."""
    try:
        display_name = name or email
        inner = f"""<h2 style="color:#e5e7eb;font-size:22px;font-weight:600;margin:0 0 8px;">Welcome, {display_name} 🎉</h2>
              <p style="color:#94a3b8;font-size:15px;margin:0 0 24px;line-height:1.6;">
                Your Ai_Generator.com account (<strong style="color:#c7d2fe;">{email}</strong>)
                is verified and ready to go. Start generating with our AI tools right away.
              </p>
              <p style="color:#64748b;font-size:13px;line-height:1.7;margin:0;">
                If you didn't create this account, please contact support immediately.
              </p>"""
        return _shell(inner)
    except Exception as exc:
        raise TemplateRenderError("Failed to render welcome email template.") from exc


def render_password_changed_email(email: str) -> str:
    """Render the password-changed security notification email."""
    try:
        inner = f"""<h2 style="color:#e5e7eb;font-size:22px;font-weight:600;margin:0 0 8px;">Your Password Was Changed</h2>
              <p style="color:#94a3b8;font-size:15px;margin:0 0 24px;line-height:1.6;">
                The password for <strong style="color:#c7d2fe;">{email}</strong> was just updated.
              </p>
              <p style="color:#64748b;font-size:13px;line-height:1.7;margin:0;">
                If you didn't make this change, please reset your password immediately and
                contact support.
              </p>"""
        return _shell(inner)
    except Exception as exc:
        raise TemplateRenderError("Failed to render password-changed email template.") from exc
