# Copyright 2024 Modernized by OCA Contributors
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MailWhatsAppInteractive(models.Model):
    """Interactive WhatsApp messages (buttons, lists)"""
    _name = "mail.whatsapp.interactive"
    _description = "WhatsApp Interactive Message"
    _order = "create_date desc"
    _rec_name = "message_type"

    gateway_id = fields.Many2one(
        "mail.gateway",
        string="Gateway",
        required=True,
        domain=[("gateway_type", "=", "whatsapp")],
        ondelete="cascade",
    )
    channel_id = fields.Many2one(
        "mail.channel",
        string="Channel",
        ondelete="set null",
    )
    message_type = fields.Selection(
        [
            ("button", "Button Message"),
            ("list", "List Message"),
            ("cta_url", "CTA URL Button"),
        ],
        string="Type",
        required=True,
        default="button",
    )
    
    # Header
    header_type = fields.Selection(
        [
            ("none", "No Header"),
            ("text", "Text"),
            ("image", "Image"),
            ("video", "Video"),
            ("document", "Document"),
        ],
        default="none",
        string="Header Type",
    )
    header_text = fields.Char(string="Header Text")
    header_media_id = fields.Char(string="Header Media ID")
    header_media_url = fields.Char(string="Header Media URL")
    
    # Body
    body_text = fields.Text(
        string="Body",
        required=True,
        help="Main message body (max 1024 characters)",
    )
    
    # Footer
    footer_text = fields.Char(
        string="Footer",
        help="Footer text (max 60 characters)",
    )
    
    # Buttons (for button type)
    button_ids = fields.One2many(
        "mail.whatsapp.interactive.button",
        "interactive_id",
        string="Buttons",
    )
    
    # List sections (for list type)
    section_ids = fields.One2many(
        "mail.whatsapp.interactive.section",
        "interactive_id",
        string="Sections",
    )
    list_button_text = fields.Char(
        string="List Button Text",
        help="Text for the list button (max 20 characters)",
        default="Ver opções",
    )
    
    # CTA URL (for cta_url type)
    cta_url = fields.Char(string="URL")
    cta_display_text = fields.Char(string="Display Text")

    def _prepare_interactive_payload(self, recipient_phone):
        """Prepare the WhatsApp API payload for interactive message"""
        self.ensure_one()
        
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_phone,
            "type": "interactive",
            "interactive": {
                "type": self.message_type,
            }
        }
        
        interactive = payload["interactive"]
        
        # Add header if present
        if self.header_type != "none":
            header = {"type": self.header_type}
            if self.header_type == "text":
                header["text"] = self.header_text
            else:
                # Media header
                header[self.header_type] = {}
                if self.header_media_id:
                    header[self.header_type]["id"] = self.header_media_id
                elif self.header_media_url:
                    header[self.header_type]["link"] = self.header_media_url
            interactive["header"] = header
        
        # Add body
        interactive["body"] = {"text": self.body_text}
        
        # Add footer if present
        if self.footer_text:
            interactive["footer"] = {"text": self.footer_text}
        
        # Add action based on message type
        if self.message_type == "button":
            buttons = []
            for btn in self.button_ids[:3]:  # Max 3 buttons
                buttons.append({
                    "type": "reply",
                    "reply": {
                        "id": btn.button_id or str(btn.id),
                        "title": btn.title[:20],  # Max 20 characters
                    }
                })
            interactive["action"] = {"buttons": buttons}
            
        elif self.message_type == "list":
            sections = []
            for section in self.section_ids:
                rows = []
                for row in section.row_ids[:10]:  # Max 10 rows per section
                    row_data = {
                        "id": row.row_id or str(row.id),
                        "title": row.title[:24],  # Max 24 characters
                    }
                    if row.description:
                        row_data["description"] = row.description[:72]  # Max 72 characters
                    rows.append(row_data)
                sections.append({
                    "title": section.title[:24],
                    "rows": rows,
                })
            interactive["action"] = {
                "button": self.list_button_text[:20] or "Ver opções",
                "sections": sections,
            }
            
        elif self.message_type == "cta_url":
            interactive["action"] = {
                "name": "cta_url",
                "parameters": {
                    "display_text": self.cta_display_text,
                    "url": self.cta_url,
                }
            }
        
        return payload

    def send_interactive_message(self, recipient_phone):
        """Send the interactive message via WhatsApp API"""
        self.ensure_one()
        
        if not recipient_phone:
            raise UserError(_("Recipient phone number is required"))
        
        # Clean phone number
        recipient_phone = recipient_phone.replace("+", "").replace(" ", "")
        
        gateway = self.gateway_id
        payload = self._prepare_interactive_payload(recipient_phone)
        
        try:
            url = f"https://graph.facebook.com/v{gateway.whatsapp_version}/{gateway.whatsapp_from_phone}/messages"
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {gateway.token}"},
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
            
            _logger.info(
                "Interactive message sent successfully: %s",
                result.get("messages", [{}])[0].get("id", "unknown")
            )
            return result
            
        except requests.exceptions.RequestException as e:
            _logger.error("Failed to send interactive message: %s", e)
            if hasattr(e, 'response') and e.response is not None:
                _logger.error("Response: %s", e.response.text)
            raise UserError(_("Failed to send interactive message: %s") % str(e)) from e


class MailWhatsAppInteractiveButton(models.Model):
    """Buttons for interactive messages"""
    _name = "mail.whatsapp.interactive.button"
    _description = "WhatsApp Interactive Button"
    _order = "sequence"

    interactive_id = fields.Many2one(
        "mail.whatsapp.interactive",
        string="Interactive Message",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    button_id = fields.Char(
        string="Button ID",
        help="Unique identifier for the button (returned in webhook)",
    )
    title = fields.Char(
        string="Title",
        required=True,
        help="Button text (max 20 characters)",
    )


class MailWhatsAppInteractiveSection(models.Model):
    """Sections for list messages"""
    _name = "mail.whatsapp.interactive.section"
    _description = "WhatsApp Interactive Section"
    _order = "sequence"

    interactive_id = fields.Many2one(
        "mail.whatsapp.interactive",
        string="Interactive Message",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    title = fields.Char(
        string="Title",
        required=True,
        help="Section title (max 24 characters)",
    )
    row_ids = fields.One2many(
        "mail.whatsapp.interactive.row",
        "section_id",
        string="Rows",
    )


class MailWhatsAppInteractiveRow(models.Model):
    """Rows for list sections"""
    _name = "mail.whatsapp.interactive.row"
    _description = "WhatsApp Interactive Row"
    _order = "sequence"

    section_id = fields.Many2one(
        "mail.whatsapp.interactive.section",
        string="Section",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    row_id = fields.Char(
        string="Row ID",
        help="Unique identifier for the row (returned in webhook)",
    )
    title = fields.Char(
        string="Title",
        required=True,
        help="Row title (max 24 characters)",
    )
    description = fields.Char(
        string="Description",
        help="Row description (max 72 characters)",
    )
