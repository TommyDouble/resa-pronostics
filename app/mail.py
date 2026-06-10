"""Email sending — uses SMTP if configured, otherwise logs to console."""
import asyncio
import json
import logging
import smtplib
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from app.config import settings

logger = logging.getLogger(__name__)


def _send_webhook_sync(to: str, subject: str, html_body: str, text_body: str = ""):
    payload = {
        "secret": settings.EMAIL_WEBHOOK_SECRET,
        "to": to,
        "subject": subject,
        "html": html_body,
        "text": text_body or "",
        "senderName": settings.EMAIL_SENDER_NAME,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        settings.EMAIL_WEBHOOK_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read(2000).decode("utf-8", errors="replace")
        if response.status >= 400:
            raise RuntimeError(f"Email webhook returned HTTP {response.status}: {body}")
    if body:
        try:
            result = json.loads(body)
        except json.JSONDecodeError:
            result = {}
        if result and not result.get("ok"):
            raise RuntimeError(result.get("error") or body)


async def send_email(to: str, subject: str, html_body: str, text_body: str = ""):
    """Send an email. Falls back to console logging if SMTP not configured."""
    if settings.EMAIL_WEBHOOK_URL:
        if not settings.EMAIL_WEBHOOK_SECRET:
            logger.error("EMAIL_WEBHOOK_SECRET is required when EMAIL_WEBHOOK_URL is configured.")
            return False
        try:
            await asyncio.to_thread(_send_webhook_sync, to, subject, html_body, text_body)
            logger.info(f"Email sent to {to} via webhook: {subject}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email to {to} via webhook: {e}")
            return False

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
    subject = "RESA Pronostics 2026 — Ton lien personnel"
    html = f"""
    <h2>Bienvenue dans RESA Pronostics 2026 !</h2>
    <p>Bonjour {participant['name']},</p>
    <p>Te voilà dans le jeu de pronostics de la Coupe du Monde 2026.
       Ce lien est ta clé d'accès personnelle — garde cet email précieusement.</p>
    <p><a href="{link}" style="background:#D3450D;color:white;padding:12px 24px;text-decoration:none;border-radius:8px;">
       Accéder à mes pronostics
    </a></p>
    <p>Ou copie ce lien : {link}</p>
    <p>Bonne chance !</p>
    """
    text = f"Bonjour {participant['name']},\n\nTon lien personnel : {link}\n"
    return await send_email(participant["email"], subject, html, text)


async def send_pre_tournament_reminder(participant: dict):
    """Remind participant to submit pre-tournament predictions."""
    link = f"{settings.BASE_URL}/p/{participant['token']}/pre-tournoi"
    subject = "RESA Pronostics — Dernière ligne droite pour le pré-tournoi !"
    html = f"""
    <h2>Rappel : pronostics pré-tournoi</h2>
    <p>Bonjour {participant['name']},</p>
    <p>Il te reste des questions pré-tournoi sans réponse (champion, finalistes,
       meilleur buteur…) et la deadline approche. Chaque réponse enregistrée
       compte — même si tout n'est pas rempli.</p>
    <p><a href="{link}" style="background:#D3450D;color:white;padding:12px 24px;text-decoration:none;border-radius:8px;">
       Compléter mes réponses
    </a></p>
    <p style="color:#6B7280;font-size:12px;">Tu peux couper ces rappels depuis ton profil.</p>
    """
    text = f"Bonjour {participant['name']},\n\nComplète tes réponses pré-tournoi avant la deadline : {link}\n"
    return await send_email(participant["email"], subject, html, text)


async def send_match_day_reminder(participant: dict, matches: list, date_label: str):
    """Rappel J-1: liste des matchs du lendemain non encore pronostiqués."""
    link = f"{settings.BASE_URL}/p/{participant['token']}/pronos"
    count = len(matches)
    subject = f"RESA Pronostics — {count} match{'s' if count > 1 else ''} demain, à toi de jouer"
    rows = "".join(
        f"<li><strong>{m['team1_name']} – {m['team2_name']}</strong>"
        f" ({(m['kickoff_local'] or '')})</li>"
        for m in matches
    )
    html = f"""
    <h2>Tes pronos pour demain ({date_label})</h2>
    <p>Bonjour {participant['name']},</p>
    <p>Il te reste <strong>{count} match{'s' if count > 1 else ''}</strong> à pronostiquer pour demain :</p>
    <ul>{rows}</ul>
    <p><a href="{link}" style="background:#D3450D;color:white;padding:12px 24px;text-decoration:none;border-radius:8px;">
       Compléter mes scores
    </a></p>
    <p style="color:#6B7280;font-size:12px;">Tu peux couper ces rappels depuis ton profil.</p>
    """
    text = (
        f"Bonjour {participant['name']},\n\n"
        f"{count} match(s) demain sans prono. Complète-les ici : {link}\n"
    )
    return await send_email(participant["email"], subject, html, text)


async def send_bonus_reminder(participant: dict, question: dict, deadline_label: str):
    """Rappel: question bonus sans réponse, deadline sous 24h."""
    link = f"{settings.BASE_URL}/p/{participant['token']}/bonus"
    subject = f"RESA Pronostics — Question bonus à {question['points_value']} pts, deadline demain"
    html = f"""
    <h2>Question bonus — dernière ligne droite</h2>
    <p>Bonjour {participant['name']},</p>
    <p>Tu n'as pas encore répondu à la question bonus
       (<strong>{question['points_value']} points</strong>) :</p>
    <blockquote>{question['question_text']}</blockquote>
    <p>Deadline : <strong>{deadline_label}</strong>.</p>
    <p><a href="{link}" style="background:#D3450D;color:white;padding:12px 24px;text-decoration:none;border-radius:8px;">
       Répondre maintenant
    </a></p>
    <p style="color:#6B7280;font-size:12px;">Tu peux couper ces rappels depuis ton profil.</p>
    """
    text = (
        f"Bonjour {participant['name']},\n\n"
        f"Question bonus sans réponse ({question['points_value']} pts), deadline {deadline_label}.\n{link}\n"
    )
    return await send_email(participant["email"], subject, html, text)


async def send_daily_recap(participant: dict, recap: dict):
    """Récap de la veille: points gagnés, rang, top 3.

    recap = {date_label, points, match_count, rank, evolution, top3: [(name, pts)]}
    """
    link = f"{settings.BASE_URL}/p/{participant['token']}"
    evolution = recap.get("evolution")
    if evolution is None or evolution == 0:
        evo_txt = ""
    elif evolution > 0:
        evo_txt = f" — tu gagnes {evolution} place{'s' if evolution > 1 else ''} 📈"
    else:
        evo_txt = f" — tu perds {-evolution} place{'s' if evolution < -1 else ''}"
    subject = f"RESA Pronostics — Hier: +{recap['points']} pts{evo_txt}"
    top3_html = "".join(
        f"<li>{name} — {pts} pts</li>" for name, pts in recap.get("top3", [])
    )
    html = f"""
    <h2>Ton récap du {recap['date_label']}</h2>
    <p>Bonjour {participant['name']},</p>
    <p>Hier tu as gagné <strong>+{recap['points']} points</strong>
       sur {recap['match_count']} match{'s' if recap['match_count'] > 1 else ''}.
       Tu es maintenant <strong>{recap['rank']}e</strong> au classement général{evo_txt}.</p>
    <p>Le podium du moment :</p>
    <ol>{top3_html}</ol>
    <p><a href="{link}" style="background:#D3450D;color:white;padding:12px 24px;text-decoration:none;border-radius:8px;">
       Voir le classement complet
    </a></p>
    """
    text = (
        f"Bonjour {participant['name']},\n\n"
        f"Hier: +{recap['points']} pts sur {recap['match_count']} matchs. "
        f"Tu es {recap['rank']}e{evo_txt}.\n{link}\n"
    )
    return await send_email(participant["email"], subject, html, text)


async def send_match_reminder(participant: dict, match: dict):
    """Remind participant to predict an upcoming match."""
    link = f"{settings.BASE_URL}/p/{participant['token']}/pronos"
    subject = f"RESA Pronostics — {match['team1_name']} vs {match['team2_name']}, ton prono ?"
    html = f"""
    <h2>Match à pronostiquer !</h2>
    <p>Bonjour {participant['name']},</p>
    <p>Tu n'as pas encore donné de score pour
       <strong>{match['team1_name']} vs {match['team2_name']}</strong>
       ({match['match_date']} à {match['kickoff_time']} UTC).</p>
    <p><a href="{link}" style="background:#D3450D;color:white;padding:12px 24px;text-decoration:none;border-radius:8px;">
       Donner mon score
    </a></p>
    <p style="color:#6B7280;font-size:12px;">Tu peux couper ces rappels depuis ton profil.</p>
    """
    text = f"Bonjour {participant['name']},\n\nDonne ton score pour {match['team1_name']} vs {match['team2_name']} : {link}\n"
    return await send_email(participant["email"], subject, html, text)
