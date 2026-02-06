# Copyright 2024 Modernized by OCA Contributors
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging
import requests
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MailGatewayEvolution(models.Model):
    """
    Evolution API Provider Configuration.
    
    Allows WhatsApp integration via QR code scanning, similar to WhatsApp Web.
    This provides an alternative to the official WhatsApp Business Cloud API.
    """
    _name = "mail.gateway.evolution"
    _description = "Evolution API WhatsApp Provider"
    _rec_name = "instance_name"
    _order = "create_date desc"

    # Basic Configuration
    gateway_id = fields.Many2one(
        "mail.gateway",
        string="Gateway",
        required=True,
        ondelete="cascade",
        domain=[("gateway_type", "=", "whatsapp")],
    )
    
    instance_name = fields.Char(
        string="Instance Name",
        required=True,
        help="Unique name for this WhatsApp instance",
    )
    
    # Evolution API Settings
    api_url = fields.Char(
        string="API URL",
        required=True,
        default="https://whatsapp.odontowave.com",
        help="Evolution API base URL (without /api)",
    )
    api_key = fields.Char(
        string="API Key",
        required=True,
        help="Authentication key for Evolution API (min 32 chars)",
    )
    
    # Connection Status
    state = fields.Selection(
        [
            ("draft", "Not Connected"),
            ("connecting", "Connecting"),
            ("qr_ready", "QR Code Ready"),
            ("connected", "Connected"),
            ("disconnected", "Disconnected"),
            ("error", "Error"),
        ],
        default="draft",
        string="Status",
        readonly=True,
    )
    
    # QR Code
    qrcode_base64 = fields.Text(
        string="QR Code",
        readonly=True,
        help="Base64 encoded QR code image",
    )
    qrcode_expiry = fields.Datetime(
        string="QR Expiry",
        readonly=True,
    )
    
    # Connected Phone Info
    phone_number = fields.Char(
        string="Phone Number",
        readonly=True,
    )
    phone_name = fields.Char(
        string="Phone Name",
        readonly=True,
    )
    connected_at = fields.Datetime(
        string="Connected At",
        readonly=True,
    )
    
    # Webhook Configuration
    webhook_url = fields.Char(
        string="Webhook URL",
        readonly=True,
        compute="_compute_webhook_url",
    )
    webhook_configured = fields.Boolean(
        string="Webhook Active",
        readonly=True,
    )
    
    # Statistics
    messages_sent = fields.Integer(
        string="Messages Sent",
        readonly=True,
    )
    messages_received = fields.Integer(
        string="Messages Received",
        readonly=True,
    )
    last_activity = fields.Datetime(
        string="Last Activity",
        readonly=True,
    )
    
    # Error handling
    error_message = fields.Text(
        string="Last Error",
        readonly=True,
    )

    _sql_constraints = [
        (
            "instance_name_unique",
            "UNIQUE(instance_name)",
            "Instance name must be unique!",
        ),
    ]

    @api.depends("gateway_id")
    def _compute_webhook_url(self):
        """Generate webhook URL for Evolution API callbacks"""
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        for record in self:
            if record.gateway_id:
                record.webhook_url = f"{base_url}/mail_gateway/{record.gateway_id.id}/evolution/webhook"
            else:
                record.webhook_url = False

    def _get_api_headers(self):
        """Get headers for Evolution API requests"""
        return {
            "Content-Type": "application/json",
            "apikey": self.api_key,
        }

    def _normalize_api_url(self, url):
        """Remove trailing /api to prevent duplication"""
        import re
        return re.sub(r"/api/?$", "", url.rstrip("/"))

    # ===================
    # INSTANCE MANAGEMENT
    # ===================

    def action_create_instance(self):
        """Create a new WhatsApp instance on Evolution API"""
        self.ensure_one()
        
        try:
            url = f"{self._normalize_api_url(self.api_url)}/api/instance/create"
            
            response = requests.post(
                url,
                headers=self._get_api_headers(),
                json={"instanceName": self.instance_name},
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
            
            self.write({
                "state": "connecting",
                "error_message": False,
            })
            
            _logger.info("Evolution instance created: %s", self.instance_name)
            
            # Automatically fetch QR code
            self.action_refresh_qrcode()
            
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Instance Created"),
                    "message": _("Instance '%s' created. Scan the QR code to connect.") % self.instance_name,
                    "type": "success",
                }
            }
            
        except requests.exceptions.RequestException as e:
            error_msg = str(e)
            if hasattr(e, "response") and e.response is not None:
                try:
                    error_msg = e.response.json().get("error", str(e))
                except Exception:
                    pass
            
            self.write({
                "state": "error",
                "error_message": error_msg,
            })
            raise UserError(_("Failed to create instance: %s") % error_msg)

    def action_refresh_qrcode(self):
        """Fetch QR code from Evolution API"""
        self.ensure_one()
        
        try:
            url = f"{self._normalize_api_url(self.api_url)}/api/instance/qrcode/{self.instance_name}"
            
            response = requests.get(
                url,
                headers=self._get_api_headers(),
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
            
            qrcode = result.get("qrcode")
            status = result.get("status")
            
            if qrcode:
                self.write({
                    "qrcode_base64": qrcode,
                    "qrcode_expiry": fields.Datetime.now() + timedelta(minutes=1),
                    "state": "qr_ready",
                    "error_message": False,
                })
            elif status == "connected":
                self.action_check_status()
            
        except requests.exceptions.RequestException as e:
            _logger.error("Failed to get QR code: %s", e)
            self.write({
                "error_message": str(e),
            })

    def action_check_status(self):
        """Check connection status on Evolution API"""
        self.ensure_one()
        
        try:
            url = f"{self._normalize_api_url(self.api_url)}/api/instance/status/{self.instance_name}"
            
            response = requests.get(
                url,
                headers=self._get_api_headers(),
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
            
            status = result.get("status", "disconnected")
            
            state_mapping = {
                "connecting": "connecting",
                "qr_ready": "qr_ready",
                "connected": "connected",
                "disconnected": "disconnected",
            }
            
            new_state = state_mapping.get(status, "disconnected")
            
            update_vals = {
                "state": new_state,
                "error_message": False,
            }
            
            if new_state == "connected":
                update_vals.update({
                    "connected_at": fields.Datetime.now(),
                    "qrcode_base64": False,
                })
                
                # Get phone info if available
                phone_info = result.get("phoneInfo", {})
                if phone_info:
                    update_vals["phone_number"] = phone_info.get("wid", {}).get("user")
                    update_vals["phone_name"] = phone_info.get("pushName")
            
            self.write(update_vals)
            
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Status Updated"),
                    "message": _("Connection status: %s") % new_state.upper(),
                    "type": "success" if new_state == "connected" else "warning",
                }
            }
            
        except requests.exceptions.RequestException as e:
            self.write({
                "state": "error",
                "error_message": str(e),
            })
            raise UserError(_("Failed to check status: %s") % e)

    def action_disconnect(self):
        """Disconnect and delete the instance"""
        self.ensure_one()
        
        try:
            url = f"{self._normalize_api_url(self.api_url)}/api/instance/delete/{self.instance_name}"
            
            response = requests.delete(
                url,
                headers=self._get_api_headers(),
                timeout=30,
            )
            response.raise_for_status()
            
            self.write({
                "state": "disconnected",
                "qrcode_base64": False,
                "phone_number": False,
                "phone_name": False,
                "connected_at": False,
                "webhook_configured": False,
            })
            
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Disconnected"),
                    "message": _("WhatsApp instance disconnected successfully."),
                    "type": "success",
                }
            }
            
        except requests.exceptions.RequestException as e:
            raise UserError(_("Failed to disconnect: %s") % e)

    def action_configure_webhook(self):
        """Configure webhook on Evolution API to receive messages"""
        self.ensure_one()
        
        if not self.webhook_url:
            raise UserError(_("Webhook URL not available. Configure the gateway first."))
        
        try:
            url = f"{self._normalize_api_url(self.api_url)}/api/webhook/set/{self.instance_name}"
            
            response = requests.post(
                url,
                headers=self._get_api_headers(),
                json={"webhookUrl": self.webhook_url},
                timeout=30,
            )
            response.raise_for_status()
            
            self.write({
                "webhook_configured": True,
                "error_message": False,
            })
            
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Webhook Configured"),
                    "message": _("Webhook configured. You will now receive incoming messages."),
                    "type": "success",
                }
            }
            
        except requests.exceptions.RequestException as e:
            raise UserError(_("Failed to configure webhook: %s") % e)

    # ==================
    # MESSAGE SENDING
    # ==================

    def send_text_message(self, phone, text):
        """Send a text message via Evolution API"""
        self.ensure_one()
        
        if self.state != "connected":
            raise UserError(_("Instance not connected. Please scan QR code first."))
        
        try:
            url = f"{self._normalize_api_url(self.api_url)}/api/message/text/{self.instance_name}"
            
            response = requests.post(
                url,
                headers=self._get_api_headers(),
                json={
                    "number": phone.replace("+", "").replace(" ", ""),
                    "text": text,
                },
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
            
            self.write({
                "messages_sent": self.messages_sent + 1,
                "last_activity": fields.Datetime.now(),
            })
            
            return result
            
        except requests.exceptions.RequestException as e:
            _logger.error("Failed to send message via Evolution: %s", e)
            raise UserError(_("Failed to send message: %s") % e)

    def send_image_message(self, phone, image_url, caption=None):
        """Send an image message via Evolution API"""
        self.ensure_one()
        
        if self.state != "connected":
            raise UserError(_("Instance not connected."))
        
        try:
            url = f"{self._normalize_api_url(self.api_url)}/api/message/image/{self.instance_name}"
            
            response = requests.post(
                url,
                headers=self._get_api_headers(),
                json={
                    "number": phone.replace("+", "").replace(" ", ""),
                    "imageUrl": image_url,
                    "caption": caption or "",
                },
                timeout=60,
            )
            response.raise_for_status()
            result = response.json()
            
            self.write({
                "messages_sent": self.messages_sent + 1,
                "last_activity": fields.Datetime.now(),
            })
            
            return result
            
        except requests.exceptions.RequestException as e:
            raise UserError(_("Failed to send image: %s") % e)

    def send_document_message(self, phone, document_url, filename, caption=None):
        """Send a document via Evolution API"""
        self.ensure_one()
        
        if self.state != "connected":
            raise UserError(_("Instance not connected."))
        
        try:
            url = f"{self._normalize_api_url(self.api_url)}/api/message/document/{self.instance_name}"
            
            response = requests.post(
                url,
                headers=self._get_api_headers(),
                json={
                    "number": phone.replace("+", "").replace(" ", ""),
                    "documentUrl": document_url,
                    "fileName": filename,
                    "caption": caption or "",
                },
                timeout=60,
            )
            response.raise_for_status()
            result = response.json()
            
            self.write({
                "messages_sent": self.messages_sent + 1,
                "last_activity": fields.Datetime.now(),
            })
            
            return result
            
        except requests.exceptions.RequestException as e:
            raise UserError(_("Failed to send document: %s") % e)

    def send_audio_message(self, phone, audio_url, ptt=True):
        """Send an audio message via Evolution API"""
        self.ensure_one()
        
        if self.state != "connected":
            raise UserError(_("Instance not connected."))
        
        try:
            url = f"{self._normalize_api_url(self.api_url)}/api/message/audio/{self.instance_name}"
            
            response = requests.post(
                url,
                headers=self._get_api_headers(),
                json={
                    "number": phone.replace("+", "").replace(" ", ""),
                    "audioUrl": audio_url,
                    "ptt": ptt,
                },
                timeout=60,
            )
            response.raise_for_status()
            result = response.json()
            
            self.write({
                "messages_sent": self.messages_sent + 1,
                "last_activity": fields.Datetime.now(),
            })
            
            return result
            
        except requests.exceptions.RequestException as e:
            raise UserError(_("Failed to send audio: %s") % e)

    # ==================
    # CRON JOBS
    # ==================

    @api.model
    def _cron_check_connections(self):
        """Check status of all Evolution instances"""
        instances = self.search([
            ("state", "in", ["connected", "connecting", "qr_ready"])
        ])
        
        for instance in instances:
            try:
                instance.action_check_status()
            except Exception as e:
                _logger.error("Failed to check instance %s: %s", instance.instance_name, e)

    @api.model
    def _cron_refresh_qrcodes(self):
        """Refresh expired QR codes"""
        expired = self.search([
            ("state", "=", "qr_ready"),
            ("qrcode_expiry", "<", fields.Datetime.now()),
        ])
        
        for instance in expired:
            try:
                instance.action_refresh_qrcode()
            except Exception as e:
                _logger.error("Failed to refresh QR for %s: %s", instance.instance_name, e)
