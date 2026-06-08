"""Email sending — uses SMTP if configured, otherwise logs to console."""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from app.config import settings

logger = logging.getLogger(__name__)


async def send_email(to: str, subject: str, html_body: str, text_body: str = ""):
    """Send an email. Falls back to console logging if SMTP not configured."""
    if not settings.SMTP_HOST:
        logger.info(
            f"[EMAIL LOG - SMTP not configured]\n"
            f"TO: {to}\n"
            f"SUBJECT: {subject}\n"
            f"BODY: {text_body or html_body}\n"
        )
        return True

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.SMTP_FROM
        msg["To"] = to

        if text_body:
            msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            if settings.SMTP_USER:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_FROM, to, msg.as_string())

        logger.info(f"Email sent to {to}: {subject}")
        return True

    except Exception as e:
        logger.error(f"Failed to send email to {to}: {e}")
        return False


async def send_invitation(participant: dict):
    """Send participation invitation with personal link."""
    link = f"{settings.BASE_URL}/p/{participant['token']}"
    subject = "RESA Pronostics 2026 - Votre lien personnel"
    html = f"""
    <h2>Bienvenue dans RESA Pronostics 2026 !</h2>
    <p>Bonjour {participant['name']},</p>
    <p>Vous êtes invité(e) à participer au jeu de pronostics de la Coupe du Monde 2026.</p>
    <p><a href="{link}" style="background:#D3450D;color:white;padding:12px 24px;text-decoration:none;border-radius:8px;">
       Accéder à mes pronostics
    </a></p>
    <p>Ou copiez ce lien : {link}</p>
    <p>Bonne chance !</p>
    """
    text = f"Bonjour {participant['name']},\n\nVotre lien : {link}\n"
    return await send_email(participant["email"], subject, html, text)


async def send_pre_tournament_reminder(participant: dict):
    """Remind participant to submit pre-tournament predictions."""
    link = f"{settings.BASE_URL}/p/{participant['token']}/pre-tournoi"
    subject = "RESA Pronostics 2026 - N'oubliez pas vos pronostics de tournoi !"
    html = f"""
    <h2>Rappel : Pronostics pré-tournoi</h2>
    <p>Bonjour {participant['name']},</p>
    <p>Vous n'avez pas encore soumis vos pronostics pré-tournoi.</p>
    <p>Dépêchez-vous, le tournoi commence bientôt !</p>
    <p><a href="{link}" style="background:#D3450D;color:white;padding:12px 24px;text-decoration:none;border-radius:8px;">
       Soumettre mes pronostics
    </a></p>
    """
    text = f"Bonjour {participant['name']},\n\nSoumettez vos pronostics : {link}\n"
    return await send_email(participant["email"], subject, html, text)


async def send_match_reminder(participant: dict, match: dict):
    """Remind participant to predict an upcoming match."""
    link = f"{settings.BASE_URL}/p/{participant['token']}/pronos"
    subject = f"RESA Pronostics - {match['team1_name']} vs {match['team2_name']}"
    html = f"""
    <h2>Match à pronostiquer !</h2>
    <p>Bonjour {participant['name']},</p>
    <p><strong>{match['team1_name']} vs {match['team2_name']}</strong>
       le {match['match_date']} à {match['kickoff_time']}</p>
    <p><a href="{link}" style="background:#D3450D;color:white;padding:12px 24px;text-decoration:none;border-radius:8px;">
       Pronostiquer
    </a></p>
    """
    text = f"Bonjour {participant['name']},\n\nPronostiquez : {link}\n"
    return await send_email(participant["email"], subject, html, text)
