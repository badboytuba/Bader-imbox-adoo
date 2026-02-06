# Copyright 2024 Modernized by OCA Contributors
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class MailWhatsappMessageStatus(models.Model):
    """Track WhatsApp message delivery status (sent, delivered, read, failed)"""
    _name = "mail.whatsapp.message.status"
    _description = "WhatsApp Message Status"
    _order = "timestamp desc"
    _rec_name = "whatsapp_message_id"

    message_id = fields.Many2one(
        "mail.message",
        string="Mail Message",
        ondelete="cascade",
        index=True,
    )
    notification_id = fields.Many2one(
        "mail.notification",
        string="Notification",
        ondelete="cascade",
        index=True,
    )
    whatsapp_message_id = fields.Char(
        string="WhatsApp Message ID",
        help="The wamid.xxx identifier from WhatsApp",
        index=True,
    )
    recipient_id = fields.Char(
        string="Recipient Phone",
        help="Phone number of the recipient",
    )
    status = fields.Selection(
        [
            ("sent", "Enviado"),
            ("delivered", "Entregue"),
            ("read", "Lido"),
            ("failed", "Falhou"),
        ],
        string="Status",
        default="sent",
        index=True,
    )
    timestamp = fields.Datetime(
        string="Timestamp",
        default=fields.Datetime.now,
    )
    sent_timestamp = fields.Datetime(string="Sent At")
    delivered_timestamp = fields.Datetime(string="Delivered At")
    read_timestamp = fields.Datetime(string="Read At")
    failed_timestamp = fields.Datetime(string="Failed At")
    
    # Error tracking
    error_code = fields.Char(string="Error Code")
    error_title = fields.Char(string="Error Title")
    error_message = fields.Text(string="Error Message")
    error_details = fields.Text(string="Error Details")
    
    # Computed fields for display
    status_icon = fields.Char(compute="_compute_status_icon", string="Status Icon")
    is_failed = fields.Boolean(compute="_compute_is_failed", store=True)

    @api.depends("status")
    def _compute_status_icon(self):
        """Return visual icon for status display in chatter"""
        icons = {
            "sent": "✓",
            "delivered": "✓✓",
            "read": "✓✓",  # Will be styled blue in UI
            "failed": "✗",
        }
        for record in self:
            record.status_icon = icons.get(record.status, "")

    @api.depends("status")
    def _compute_is_failed(self):
        for record in self:
            record.is_failed = record.status == "failed"

    def update_status(self, new_status, timestamp=None, error_info=None):
        """Update the status with proper timestamp tracking"""
        self.ensure_one()
        vals = {
            "status": new_status,
            "timestamp": timestamp or fields.Datetime.now(),
        }
        
        # Set specific timestamp field
        if new_status == "sent":
            vals["sent_timestamp"] = vals["timestamp"]
        elif new_status == "delivered":
            vals["delivered_timestamp"] = vals["timestamp"]
        elif new_status == "read":
            vals["read_timestamp"] = vals["timestamp"]
        elif new_status == "failed":
            vals["failed_timestamp"] = vals["timestamp"]
            if error_info:
                vals.update({
                    "error_code": error_info.get("code"),
                    "error_title": error_info.get("title"),
                    "error_message": error_info.get("message"),
                    "error_details": error_info.get("details"),
                })
        
        self.write(vals)
        
        # Update the notification status if linked
        if self.notification_id and new_status == "failed":
            self.notification_id.write({
                "notification_status": "exception",
                "failure_reason": error_info.get("message") if error_info else "Unknown error",
            })
            self.notification_id.mail_message_id._notify_message_notification_update()
        
        return self
