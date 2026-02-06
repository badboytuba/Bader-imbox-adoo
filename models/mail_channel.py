# Copyright 2024 Dixmit
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from datetime import timedelta

from odoo import api, fields, models
from odoo.modules.module import get_resource_path

from odoo.addons.base.models.avatar_mixin import get_hsl_from_seed


class MailChannel(models.Model):

    _inherit = "mail.channel"

    # WhatsApp 24-hour window tracking
    whatsapp_last_customer_message = fields.Datetime(
        string="Last Customer Message",
        help="Timestamp of the last message received from the customer. "
             "Used to track the 24-hour messaging window.",
    )
    whatsapp_window_active = fields.Boolean(
        compute="_compute_whatsapp_window",
        string="Window Active",
        help="Whether the 24-hour messaging window is still open.",
    )
    whatsapp_window_expires_at = fields.Datetime(
        compute="_compute_whatsapp_window",
        string="Window Expires At",
        help="When the 24-hour messaging window will expire.",
    )
    whatsapp_requires_template = fields.Boolean(
        compute="_compute_whatsapp_window",
        string="Requires Template",
        help="Whether a template is required to send a message (window closed).",
    )
    whatsapp_window_hours_remaining = fields.Float(
        compute="_compute_whatsapp_window",
        string="Hours Remaining",
        help="Hours remaining in the messaging window.",
    )

    @api.depends("whatsapp_last_customer_message", "gateway_id", "gateway_id.gateway_type")
    def _compute_whatsapp_window(self):
        """Compute the state of the 24-hour messaging window"""
        now = fields.Datetime.now()
        for channel in self:
            if channel.gateway_id and channel.gateway_id.gateway_type == "whatsapp":
                if channel.whatsapp_last_customer_message:
                    expires_at = channel.whatsapp_last_customer_message + timedelta(hours=24)
                    channel.whatsapp_window_expires_at = expires_at
                    channel.whatsapp_window_active = now < expires_at
                    channel.whatsapp_requires_template = now >= expires_at
                    
                    # Calculate hours remaining
                    if channel.whatsapp_window_active:
                        delta = expires_at - now
                        channel.whatsapp_window_hours_remaining = delta.total_seconds() / 3600
                    else:
                        channel.whatsapp_window_hours_remaining = 0.0
                else:
                    # No customer message yet - window closed, template required
                    channel.whatsapp_window_expires_at = False
                    channel.whatsapp_window_active = False
                    channel.whatsapp_requires_template = True
                    channel.whatsapp_window_hours_remaining = 0.0
            else:
                # Not a WhatsApp channel
                channel.whatsapp_window_expires_at = False
                channel.whatsapp_window_active = True
                channel.whatsapp_requires_template = False
                channel.whatsapp_window_hours_remaining = 24.0

    def _update_whatsapp_last_customer_message(self):
        """Update the last customer message timestamp when a message is received"""
        self.ensure_one()
        if self.gateway_id and self.gateway_id.gateway_type == "whatsapp":
            self.whatsapp_last_customer_message = fields.Datetime.now()

    def _generate_avatar_gateway(self):
        if self.gateway_id.gateway_type == "whatsapp":
            path = get_resource_path(
                "bader_inbox", "static/description", "icon.svg"
            )
            with open(path, "r") as f:
                avatar = f.read()

            bgcolor = get_hsl_from_seed(self.uuid)
            avatar = avatar.replace("fill:#875a7b", f"fill:{bgcolor}")
            return avatar
        return super()._generate_avatar_gateway()

    def get_whatsapp_window_status(self):
        """Return window status for frontend display"""
        self.ensure_one()
        if not self.gateway_id or self.gateway_id.gateway_type != "whatsapp":
            return {}
        
        return {
            "window_active": self.whatsapp_window_active,
            "requires_template": self.whatsapp_requires_template,
            "hours_remaining": round(self.whatsapp_window_hours_remaining, 1),
            "expires_at": self.whatsapp_window_expires_at.isoformat() if self.whatsapp_window_expires_at else None,
            "last_customer_message": self.whatsapp_last_customer_message.isoformat() if self.whatsapp_last_customer_message else None,
        }
