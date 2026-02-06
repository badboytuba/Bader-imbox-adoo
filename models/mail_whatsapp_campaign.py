# Copyright 2024 Modernized by OCA Contributors
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging
import time
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MailWhatsAppCampaign(models.Model):
    """
    WhatsApp Marketing Campaigns.
    
    Send template messages to multiple recipients with rate limiting
    and delivery tracking.
    """
    _name = "mail.whatsapp.campaign"
    _description = "WhatsApp Campaign"
    _order = "create_date desc"
    _rec_name = "name"

    name = fields.Char(required=True)
    gateway_id = fields.Many2one(
        "mail.gateway",
        string="Gateway",
        required=True,
        domain=[("gateway_type", "=", "whatsapp")],
        ondelete="cascade",
    )
    template_id = fields.Many2one(
        "mail.whatsapp.template",
        string="Template",
        required=True,
        domain="[('gateway_id', '=', gateway_id), ('state', '=', 'approved')]",
    )
    
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("scheduled", "Scheduled"),
            ("running", "Running"),
            ("paused", "Paused"),
            ("completed", "Completed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        index=True,
    )
    
    # Recipients
    recipient_model = fields.Selection(
        [
            ("res.partner", "Contacts"),
            ("crm.lead", "Leads"),
            ("mailing.list", "Mailing List"),
            ("manual", "Manual List"),
        ],
        string="Recipient Source",
        default="res.partner",
        required=True,
    )
    recipient_domain = fields.Char(
        string="Filter",
        default="[('phone', '!=', False)]",
        help="Domain filter for recipients",
    )
    partner_ids = fields.Many2many(
        "res.partner",
        string="Contacts",
        domain="[('phone', '!=', False)]",
    )
    manual_phones = fields.Text(
        string="Phone Numbers",
        help="One phone number per line (with country code)",
    )
    
    # Template variables
    variable_mapping = fields.Text(
        string="Variable Mapping",
        help='JSON mapping: {"1": "name", "2": "phone"}',
        default='{}',
    )
    
    # Scheduling
    scheduled_datetime = fields.Datetime(string="Scheduled For")
    
    # Rate limiting
    rate_limit = fields.Integer(
        string="Messages/Hour",
        default=100,
        help="Maximum messages per hour (0 = unlimited)",
    )
    batch_size = fields.Integer(
        string="Batch Size",
        default=20,
        help="Messages to send in each batch",
    )
    batch_delay = fields.Integer(
        string="Batch Delay (seconds)",
        default=5,
        help="Seconds between batches",
    )
    
    # Progress
    total_recipients = fields.Integer(
        string="Total Recipients",
        readonly=True,
    )
    sent_count = fields.Integer(
        string="Sent",
        readonly=True,
    )
    delivered_count = fields.Integer(
        string="Delivered",
        readonly=True,
    )
    read_count = fields.Integer(
        string="Read",
        readonly=True,
    )
    failed_count = fields.Integer(
        string="Failed",
        readonly=True,
    )
    progress = fields.Float(
        compute="_compute_progress",
        string="Progress %",
    )
    
    # Timestamps
    started_at = fields.Datetime(string="Started At")
    completed_at = fields.Datetime(string="Completed At")
    
    # Message references
    message_ids = fields.One2many(
        "mail.whatsapp.campaign.message",
        "campaign_id",
        string="Messages",
    )

    @api.depends("total_recipients", "sent_count")
    def _compute_progress(self):
        for record in self:
            if record.total_recipients:
                record.progress = (record.sent_count / record.total_recipients) * 100
            else:
                record.progress = 0

    def action_prepare(self):
        """Prepare campaign - calculate recipients"""
        self.ensure_one()
        
        recipients = self._get_recipients()
        
        # Clear existing message records
        self.message_ids.unlink()
        
        # Create message records for each recipient
        CampaignMessage = self.env["mail.whatsapp.campaign.message"]
        for phone, partner_id, data in recipients:
            CampaignMessage.create({
                "campaign_id": self.id,
                "phone": phone,
                "partner_id": partner_id,
                "variable_data": str(data),
                "state": "pending",
            })
        
        self.write({
            "total_recipients": len(recipients),
            "state": "draft",
        })
        
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Campaign Prepared"),
                "message": _("Found %d recipients") % len(recipients),
                "type": "success",
            }
        }

    def _get_recipients(self):
        """Get list of (phone, partner_id, variable_data) tuples"""
        self.ensure_one()
        recipients = []
        
        import json
        var_mapping = json.loads(self.variable_mapping or "{}")
        
        if self.recipient_model == "manual":
            # Manual phone list
            if self.manual_phones:
                for phone in self.manual_phones.strip().split("\n"):
                    phone = phone.strip()
                    if phone:
                        recipients.append((phone, False, {}))
        
        elif self.recipient_model == "res.partner":
            # Partner records
            domain = eval(self.recipient_domain or "[]")  # noqa: S307
            if self.partner_ids:
                domain.append(("id", "in", self.partner_ids.ids))
            
            partners = self.env["res.partner"].search(domain)
            
            for partner in partners:
                phone = partner.mobile or partner.phone
                if phone:
                    phone = phone.replace(" ", "").replace("-", "")
                    if not phone.startswith("+"):
                        phone = "+" + phone
                    
                    # Build variable data
                    data = {}
                    for var_num, field_name in var_mapping.items():
                        data[var_num] = getattr(partner, field_name, "") or ""
                    
                    recipients.append((phone, partner.id, data))
        
        elif self.recipient_model == "crm.lead":
            # Leads
            domain = eval(self.recipient_domain or "[]")  # noqa: S307
            leads = self.env["crm.lead"].search(domain)
            
            for lead in leads:
                phone = lead.mobile or lead.phone
                if phone:
                    phone = phone.replace(" ", "").replace("-", "")
                    if not phone.startswith("+"):
                        phone = "+" + phone
                    
                    data = {}
                    for var_num, field_name in var_mapping.items():
                        data[var_num] = getattr(lead, field_name, "") or ""
                    
                    recipients.append((phone, lead.partner_id.id if lead.partner_id else False, data))
        
        return recipients

    def action_start(self):
        """Start sending campaign"""
        self.ensure_one()
        
        if not self.message_ids:
            raise UserError(_("Please prepare the campaign first"))
        
        self.write({
            "state": "running",
            "started_at": fields.Datetime.now(),
        })
        
        # Process in batches
        self._send_batch()

    def action_pause(self):
        """Pause campaign"""
        self.write({"state": "paused"})

    def action_resume(self):
        """Resume paused campaign"""
        self.write({"state": "running"})
        self._send_batch()

    def action_cancel(self):
        """Cancel campaign"""
        self.write({"state": "cancelled"})

    def _send_batch(self):
        """Send a batch of messages"""
        self.ensure_one()
        
        if self.state != "running":
            return
        
        # Get pending messages
        pending = self.message_ids.filtered(lambda m: m.state == "pending")
        batch = pending[:self.batch_size]
        
        if not batch:
            # Campaign completed
            self.write({
                "state": "completed",
                "completed_at": fields.Datetime.now(),
            })
            return
        
        # Send each message in batch
        WhatsApp = self.env["mail.gateway.whatsapp"]
        gateway = self.gateway_id
        
        import json
        
        for msg in batch:
            try:
                var_data = json.loads(msg.variable_data or "{}")
                
                # Send template message
                result = WhatsApp._send_template_message(
                    gateway,
                    msg.phone,
                    self.template_id,
                    var_data,
                )
                
                msg.write({
                    "state": "sent",
                    "sent_at": fields.Datetime.now(),
                    "whatsapp_message_id": result.get("messages", [{}])[0].get("id"),
                })
                self.sent_count += 1
                
            except Exception as e:
                msg.write({
                    "state": "failed",
                    "error_message": str(e),
                })
                self.failed_count += 1
            
            # Rate limiting delay
            if self.rate_limit:
                time.sleep(3600 / self.rate_limit)
        
        # Schedule next batch
        if self.state == "running":
            self.env.ref("bader_inbox.ir_cron_campaign_batch")._trigger(
                at=fields.Datetime.now() + timedelta(seconds=self.batch_delay)
            )

    @api.model
    def _cron_process_campaigns(self):
        """Process running campaigns"""
        campaigns = self.search([("state", "=", "running")])
        for campaign in campaigns:
            try:
                campaign._send_batch()
            except Exception as e:
                _logger.error("Campaign %s error: %s", campaign.name, e)


class MailWhatsAppCampaignMessage(models.Model):
    """Individual message in a campaign"""
    _name = "mail.whatsapp.campaign.message"
    _description = "WhatsApp Campaign Message"
    _order = "id"

    campaign_id = fields.Many2one(
        "mail.whatsapp.campaign",
        string="Campaign",
        required=True,
        ondelete="cascade",
        index=True,
    )
    phone = fields.Char(required=True)
    partner_id = fields.Many2one(
        "res.partner",
        string="Contact",
        ondelete="set null",
    )
    variable_data = fields.Text(string="Variables")
    
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("sent", "Sent"),
            ("delivered", "Delivered"),
            ("read", "Read"),
            ("failed", "Failed"),
        ],
        default="pending",
        required=True,
        index=True,
    )
    
    whatsapp_message_id = fields.Char(string="Message ID")
    sent_at = fields.Datetime(string="Sent At")
    delivered_at = fields.Datetime(string="Delivered At")
    read_at = fields.Datetime(string="Read At")
    error_message = fields.Text(string="Error")
