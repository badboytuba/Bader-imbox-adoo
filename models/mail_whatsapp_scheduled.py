# Copyright 2024 Modernized by OCA Contributors
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class MailWhatsAppScheduled(models.Model):
    """
    Scheduled WhatsApp messages.
    
    Allows scheduling messages for future delivery using Odoo's cron system.
    """
    _name = "mail.whatsapp.scheduled"
    _description = "Scheduled WhatsApp Message"
    _order = "scheduled_datetime"
    _rec_name = "display_name"

    gateway_id = fields.Many2one(
        "mail.gateway",
        string="Gateway",
        required=True,
        domain=[("gateway_type", "=", "whatsapp")],
        ondelete="cascade",
    )
    channel_id = fields.Many2one(
        "mail.channel",
        string="Conversation",
        ondelete="set null",
    )
    recipient_phone = fields.Char(
        string="Recipient Phone",
        required=True,
        help="Phone number with country code (e.g., +5511999999999)",
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Contact",
        ondelete="set null",
    )
    
    # Message content
    message_type = fields.Selection(
        [
            ("text", "Text Message"),
            ("template", "Template"),
            ("interactive", "Interactive"),
        ],
        default="text",
        required=True,
    )
    body = fields.Text(string="Message Body")
    template_id = fields.Many2one(
        "mail.whatsapp.template",
        string="Template",
    )
    template_variables = fields.Text(
        string="Template Variables",
        help="JSON with variable values: {\"1\": \"value1\", \"2\": \"value2\"}",
    )
    interactive_id = fields.Many2one(
        "mail.whatsapp.interactive",
        string="Interactive Message",
    )
    
    # Scheduling
    scheduled_datetime = fields.Datetime(
        string="Send At",
        required=True,
        index=True,
    )
    timezone = fields.Selection(
        "_tz_get",
        string="Timezone",
        default=lambda self: self.env.user.tz or "UTC",
    )
    
    # Status
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("scheduled", "Scheduled"),
            ("sent", "Sent"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        index=True,
    )
    sent_datetime = fields.Datetime(string="Sent At")
    error_message = fields.Text(string="Error")
    whatsapp_message_id = fields.Char(string="WhatsApp Message ID")
    
    # Metadata
    user_id = fields.Many2one(
        "res.users",
        string="Created By",
        default=lambda self: self.env.user,
    )
    display_name = fields.Char(
        compute="_compute_display_name",
        store=True,
    )

    @api.model
    def _tz_get(self):
        return [(tz, tz) for tz in sorted(
            set(tz for tz in self.env['res.partner']._tz_get() if tz),
            key=lambda x: x[1]
        )]

    @api.depends("recipient_phone", "scheduled_datetime")
    def _compute_display_name(self):
        for record in self:
            if record.scheduled_datetime:
                dt = fields.Datetime.context_timestamp(
                    record, record.scheduled_datetime
                )
                record.display_name = f"{record.recipient_phone} - {dt.strftime('%d/%m %H:%M')}"
            else:
                record.display_name = record.recipient_phone

    def action_schedule(self):
        """Confirm and schedule the message"""
        for record in self:
            if record.state == "draft":
                record.write({"state": "scheduled"})
        return True

    def action_cancel(self):
        """Cancel scheduled message"""
        for record in self:
            if record.state in ("draft", "scheduled"):
                record.write({"state": "cancelled"})
        return True

    def action_send_now(self):
        """Send immediately instead of waiting"""
        for record in self:
            record._send_message()
        return True

    def _send_message(self):
        """Send the scheduled message"""
        self.ensure_one()
        
        try:
            WhatsAppService = self.env["mail.gateway.whatsapp"]
            gateway = self.gateway_id
            
            if self.message_type == "text":
                # Send text message
                result = WhatsAppService._send_text_message(
                    gateway,
                    self.recipient_phone,
                    self.body,
                )
            
            elif self.message_type == "template":
                # Send template message
                variables = {}
                if self.template_variables:
                    import json
                    variables = json.loads(self.template_variables)
                
                result = WhatsAppService._send_template_message(
                    gateway,
                    self.recipient_phone,
                    self.template_id,
                    variables,
                )
            
            elif self.message_type == "interactive":
                # Send interactive message
                result = self.interactive_id.send_interactive_message(
                    self.recipient_phone
                )
            
            # Update status
            message_id = result.get("messages", [{}])[0].get("id", "")
            self.write({
                "state": "sent",
                "sent_datetime": fields.Datetime.now(),
                "whatsapp_message_id": message_id,
                "error_message": False,
            })
            
            _logger.info("Scheduled message %s sent successfully", self.id)
            
        except Exception as e:
            _logger.error("Failed to send scheduled message %s: %s", self.id, e)
            self.write({
                "state": "failed",
                "error_message": str(e),
            })

    @api.model
    def _cron_send_scheduled_messages(self):
        """
        Cron job to send scheduled messages.
        
        Should be called every minute.
        """
        now = fields.Datetime.now()
        
        # Find messages ready to send
        messages = self.search([
            ("state", "=", "scheduled"),
            ("scheduled_datetime", "<=", now),
        ], limit=50)  # Process in batches
        
        for message in messages:
            try:
                message._send_message()
            except Exception as e:
                _logger.error("Cron: Failed to send message %s: %s", message.id, e)
        
        _logger.info("Cron: Processed %d scheduled messages", len(messages))
        
        return True
