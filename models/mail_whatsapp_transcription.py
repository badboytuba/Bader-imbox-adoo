# Copyright 2024 Modernized by OCA Contributors
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import base64
import logging
import tempfile
import os

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MailWhatsAppTranscription(models.Model):
    """
    Audio transcription for WhatsApp voice messages.
    
    Uses OpenAI Whisper or other speech-to-text APIs.
    """
    _name = "mail.whatsapp.transcription"
    _description = "WhatsApp Audio Transcription"
    _order = "create_date desc"

    gateway_id = fields.Many2one(
        "mail.gateway",
        string="Gateway",
        domain=[("gateway_type", "=", "whatsapp")],
        ondelete="cascade",
    )
    message_id = fields.Many2one(
        "mail.message",
        string="Message",
        ondelete="cascade",
    )
    channel_id = fields.Many2one(
        "mail.channel",
        string="Conversation",
        ondelete="set null",
    )
    
    # Audio info
    whatsapp_media_id = fields.Char(string="WhatsApp Media ID")
    audio_url = fields.Char(string="Audio URL")
    audio_duration = fields.Float(string="Duration (seconds)")
    audio_mimetype = fields.Char(string="MIME Type")
    
    # Transcription
    transcription = fields.Text(string="Transcription")
    language = fields.Char(string="Detected Language")
    confidence = fields.Float(string="Confidence")
    
    # Status
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("processing", "Processing"),
            ("completed", "Completed"),
            ("failed", "Failed"),
        ],
        default="pending",
        required=True,
    )
    error_message = fields.Text(string="Error")
    
    # Provider
    provider = fields.Selection(
        [
            ("whisper", "OpenAI Whisper"),
            ("google", "Google Speech-to-Text"),
            ("azure", "Azure Speech"),
        ],
        default="whisper",
    )

    def transcribe(self):
        """Transcribe the audio message"""
        self.ensure_one()
        
        self.write({"state": "processing"})
        
        try:
            # Download audio from WhatsApp
            audio_data = self._download_audio()
            
            if not audio_data:
                raise UserError(_("Failed to download audio"))
            
            # Transcribe based on provider
            if self.provider == "whisper":
                result = self._transcribe_whisper(audio_data)
            elif self.provider == "google":
                result = self._transcribe_google(audio_data)
            else:
                raise UserError(_("Unsupported transcription provider"))
            
            self.write({
                "transcription": result.get("text", ""),
                "language": result.get("language", ""),
                "confidence": result.get("confidence", 0),
                "state": "completed",
                "error_message": False,
            })
            
            # Update original message with transcription
            if self.message_id:
                current_body = self.message_id.body or ""
                self.message_id.write({
                    "body": f"{current_body}<br/><i>📝 Transcrição: {self.transcription}</i>"
                })
            
            _logger.info("Audio transcribed successfully: %s", self.id)
            
        except Exception as e:
            _logger.error("Transcription failed: %s", e)
            self.write({
                "state": "failed",
                "error_message": str(e),
            })

    def _download_audio(self):
        """Download audio file from WhatsApp"""
        if not self.whatsapp_media_id and not self.audio_url:
            return None
        
        gateway = self.gateway_id
        
        if self.whatsapp_media_id:
            # Get media URL from WhatsApp
            try:
                url = f"https://graph.facebook.com/v{gateway.whatsapp_version}/{self.whatsapp_media_id}"
                response = requests.get(
                    url,
                    headers={"Authorization": f"Bearer {gateway.token}"},
                    timeout=30,
                )
                response.raise_for_status()
                media_info = response.json()
                audio_url = media_info.get("url")
            except Exception as e:
                _logger.error("Failed to get media URL: %s", e)
                return None
        else:
            audio_url = self.audio_url
        
        # Download the audio file
        try:
            response = requests.get(
                audio_url,
                headers={"Authorization": f"Bearer {gateway.token}"},
                timeout=60,
            )
            response.raise_for_status()
            return response.content
        except Exception as e:
            _logger.error("Failed to download audio: %s", e)
            return None

    def _transcribe_whisper(self, audio_data):
        """Transcribe using OpenAI Whisper API"""
        # Get API key from gateway or system parameters
        api_key = self.env["ir.config_parameter"].sudo().get_param(
            "mail_gateway_whatsapp.openai_api_key"
        )
        
        if not api_key:
            raise UserError(_("OpenAI API key not configured"))
        
        # Save audio to temp file
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(audio_data)
            temp_path = f.name
        
        try:
            with open(temp_path, "rb") as audio_file:
                response = requests.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": ("audio.ogg", audio_file, "audio/ogg")},
                    data={
                        "model": "whisper-1",
                        "response_format": "verbose_json",
                    },
                    timeout=120,
                )
                response.raise_for_status()
                result = response.json()
                
                return {
                    "text": result.get("text", ""),
                    "language": result.get("language", ""),
                    "confidence": 1.0,  # Whisper doesn't return confidence
                }
        finally:
            # Cleanup temp file
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def _transcribe_google(self, audio_data):
        """Transcribe using Google Speech-to-Text API"""
        api_key = self.env["ir.config_parameter"].sudo().get_param(
            "mail_gateway_whatsapp.google_speech_api_key"
        )
        
        if not api_key:
            raise UserError(_("Google Speech API key not configured"))
        
        # Encode audio as base64
        audio_base64 = base64.b64encode(audio_data).decode()
        
        response = requests.post(
            f"https://speech.googleapis.com/v1/speech:recognize?key={api_key}",
            json={
                "config": {
                    "encoding": "OGG_OPUS",
                    "languageCode": "pt-BR",
                    "alternativeLanguageCodes": ["en-US", "es-ES"],
                    "enableAutomaticPunctuation": True,
                },
                "audio": {
                    "content": audio_base64,
                }
            },
            timeout=120,
        )
        response.raise_for_status()
        result = response.json()
        
        if result.get("results"):
            best_result = result["results"][0]["alternatives"][0]
            return {
                "text": best_result.get("transcript", ""),
                "language": result["results"][0].get("languageCode", ""),
                "confidence": best_result.get("confidence", 0),
            }
        
        return {"text": "", "language": "", "confidence": 0}

    @api.model
    def create_from_webhook(self, gateway, channel, audio_data):
        """
        Create transcription record from incoming audio message.
        
        Called from webhook processing when audio message is received.
        """
        record = self.create({
            "gateway_id": gateway.id,
            "channel_id": channel.id,
            "whatsapp_media_id": audio_data.get("id"),
            "audio_mimetype": audio_data.get("mime_type"),
            "provider": "whisper",
            "state": "pending",
        })
        
        # Auto-transcribe if enabled
        auto_transcribe = self.env["ir.config_parameter"].sudo().get_param(
            "mail_gateway_whatsapp.auto_transcribe_audio", "False"
        )
        
        if auto_transcribe == "True":
            record.transcribe()
        
        return record

    @api.model
    def _cron_process_pending(self):
        """Process pending transcriptions"""
        pending = self.search([("state", "=", "pending")], limit=10)
        
        for record in pending:
            try:
                record.transcribe()
            except Exception as e:
                _logger.error("Cron transcription failed: %s", e)
