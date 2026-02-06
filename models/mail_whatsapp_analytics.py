# Copyright 2024 Modernized by OCA Contributors
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class MailWhatsAppAnalytics(models.Model):
    """
    WhatsApp Analytics - Message and conversation metrics.
    
    Tracks key performance indicators for WhatsApp communications.
    """
    _name = "mail.whatsapp.analytics"
    _description = "WhatsApp Analytics"
    _order = "date desc"
    _rec_name = "date"

    gateway_id = fields.Many2one(
        "mail.gateway",
        string="Gateway",
        required=True,
        domain=[("gateway_type", "=", "whatsapp")],
        ondelete="cascade",
        index=True,
    )
    date = fields.Date(
        required=True,
        index=True,
        default=fields.Date.today,
    )
    
    # Message counts
    messages_sent = fields.Integer(string="Messages Sent")
    messages_received = fields.Integer(string="Messages Received")
    messages_delivered = fields.Integer(string="Delivered")
    messages_read = fields.Integer(string="Read")
    messages_failed = fields.Integer(string="Failed")
    
    # Template stats
    templates_sent = fields.Integer(string="Templates Sent")
    templates_delivered = fields.Integer(string="Templates Delivered")
    templates_read = fields.Integer(string="Templates Read")
    
    # Conversation stats
    new_conversations = fields.Integer(string="New Conversations")
    active_conversations = fields.Integer(string="Active Conversations")
    resolved_conversations = fields.Integer(string="Resolved")
    
    # Response time (in seconds)
    avg_response_time = fields.Float(string="Avg Response Time (min)")
    avg_resolution_time = fields.Float(string="Avg Resolution Time (min)")
    
    # Engagement
    button_clicks = fields.Integer(string="Button Clicks")
    list_selections = fields.Integer(string="List Selections")
    flow_completions = fields.Integer(string="Flow Completions")
    reactions_received = fields.Integer(string="Reactions")
    
    # Agent stats
    agent_messages_sent = fields.Integer(string="Agent Messages")
    automation_messages_sent = fields.Integer(string="Automation Messages")

    _sql_constraints = [
        ("gateway_date_unique", "unique(gateway_id, date)", 
         "Analytics record already exists for this date and gateway"),
    ]

    @api.model
    def get_or_create_today(self, gateway_id):
        """Get or create today's analytics record"""
        today = fields.Date.today()
        record = self.search([
            ("gateway_id", "=", gateway_id),
            ("date", "=", today),
        ], limit=1)
        
        if not record:
            record = self.create({
                "gateway_id": gateway_id,
                "date": today,
            })
        
        return record

    def increment_counter(self, field_name, amount=1):
        """Increment a counter field"""
        self.ensure_one()
        current_value = getattr(self, field_name, 0) or 0
        self.write({field_name: current_value + amount})

    @api.model
    def _cron_compute_daily_stats(self):
        """
        Compute daily analytics from raw data.
        
        Should run at end of each day.
        """
        yesterday = fields.Date.today() - timedelta(days=1)
        
        gateways = self.env["mail.gateway"].search([
            ("gateway_type", "=", "whatsapp"),
        ])
        
        for gateway in gateways:
            self._compute_stats_for_date(gateway, yesterday)
        
        _logger.info("Computed daily analytics for %d gateways", len(gateways))

    def _compute_stats_for_date(self, gateway, date):
        """Compute stats for a specific gateway and date"""
        record = self.search([
            ("gateway_id", "=", gateway.id),
            ("date", "=", date),
        ], limit=1)
        
        if not record:
            record = self.create({
                "gateway_id": gateway.id,
                "date": date,
            })
        
        # Get message status records for the date
        MessageStatus = self.env["mail.whatsapp.message.status"]
        date_start = fields.Datetime.to_datetime(date)
        date_end = date_start + timedelta(days=1)
        
        statuses = MessageStatus.search([
            ("gateway_id", "=", gateway.id),
            ("sent_at", ">=", date_start),
            ("sent_at", "<", date_end),
        ])
        
        vals = {
            "messages_sent": len(statuses),
            "messages_delivered": len(statuses.filtered(lambda s: s.status in ["delivered", "read"])),
            "messages_read": len(statuses.filtered(lambda s: s.status == "read")),
            "messages_failed": len(statuses.filtered(lambda s: s.status == "failed")),
        }
        
        # Get assignment stats
        Assignment = self.env.get("mail.whatsapp.assignment")
        if Assignment:
            assignments = Assignment.search([
                ("assigned_at", ">=", date_start),
                ("assigned_at", "<", date_end),
            ])
            
            if assignments:
                response_times = [a.response_time_seconds for a in assignments if a.response_time_seconds]
                resolution_times = [a.resolution_time_seconds for a in assignments if a.resolution_time_seconds]
                
                vals["avg_response_time"] = sum(response_times) / len(response_times) / 60 if response_times else 0
                vals["avg_resolution_time"] = sum(resolution_times) / len(resolution_times) / 60 if resolution_times else 0
                vals["resolved_conversations"] = len(assignments.filtered(lambda a: a.state == "resolved"))
        
        record.write(vals)
        return record


class MailWhatsAppAnalyticsSummary(models.TransientModel):
    """Transient model for analytics dashboard summary"""
    _name = "mail.whatsapp.analytics.summary"
    _description = "WhatsApp Analytics Summary"

    gateway_id = fields.Many2one(
        "mail.gateway",
        string="Gateway",
        domain=[("gateway_type", "=", "whatsapp")],
    )
    date_from = fields.Date(
        default=lambda self: fields.Date.today() - timedelta(days=7),
    )
    date_to = fields.Date(
        default=fields.Date.today,
    )
    
    # Computed summary fields
    total_sent = fields.Integer(compute="_compute_summary")
    total_received = fields.Integer(compute="_compute_summary")
    total_delivered = fields.Integer(compute="_compute_summary")
    total_read = fields.Integer(compute="_compute_summary")
    total_failed = fields.Integer(compute="_compute_summary")
    
    delivery_rate = fields.Float(compute="_compute_summary", string="Delivery Rate %")
    read_rate = fields.Float(compute="_compute_summary", string="Read Rate %")
    
    avg_response_time = fields.Float(compute="_compute_summary")
    new_conversations = fields.Integer(compute="_compute_summary")

    @api.depends("gateway_id", "date_from", "date_to")
    def _compute_summary(self):
        for record in self:
            domain = [
                ("date", ">=", record.date_from),
                ("date", "<=", record.date_to),
            ]
            if record.gateway_id:
                domain.append(("gateway_id", "=", record.gateway_id.id))
            
            analytics = self.env["mail.whatsapp.analytics"].search(domain)
            
            record.total_sent = sum(analytics.mapped("messages_sent"))
            record.total_received = sum(analytics.mapped("messages_received"))
            record.total_delivered = sum(analytics.mapped("messages_delivered"))
            record.total_read = sum(analytics.mapped("messages_read"))
            record.total_failed = sum(analytics.mapped("messages_failed"))
            record.new_conversations = sum(analytics.mapped("new_conversations"))
            
            # Rates
            if record.total_sent:
                record.delivery_rate = (record.total_delivered / record.total_sent) * 100
                record.read_rate = (record.total_read / record.total_sent) * 100
            else:
                record.delivery_rate = 0
                record.read_rate = 0
            
            # Average response time
            response_times = [a.avg_response_time for a in analytics if a.avg_response_time]
            record.avg_response_time = sum(response_times) / len(response_times) if response_times else 0

    def action_view_details(self):
        """Open detailed analytics view"""
        domain = [
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
        ]
        if self.gateway_id:
            domain.append(("gateway_id", "=", self.gateway_id.id))
        
        return {
            "type": "ir.actions.act_window",
            "name": "Analytics Details",
            "res_model": "mail.whatsapp.analytics",
            "view_mode": "graph,tree",
            "domain": domain,
            "context": {"group_by": "date"},
        }
