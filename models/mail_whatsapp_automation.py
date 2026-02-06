# Copyright 2024 Modernized by OCA Contributors
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import json
import logging
import re

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class MailWhatsAppAutomation(models.Model):
    """
    WhatsApp Automation Rules.
    
    Automatically execute actions based on WhatsApp events.
    """
    _name = "mail.whatsapp.automation"
    _description = "WhatsApp Automation"
    _order = "sequence, id"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    gateway_id = fields.Many2one(
        "mail.gateway",
        string="Gateway",
        domain=[("gateway_type", "=", "whatsapp")],
        ondelete="cascade",
    )
    
    # Trigger conditions
    trigger_type = fields.Selection(
        [
            ("new_conversation", "New Conversation"),
            ("message_received", "Message Received"),
            ("keyword", "Keyword Match"),
            ("button_click", "Button Click"),
            ("list_selection", "List Selection"),
            ("flow_complete", "Flow Completed"),
            ("no_response", "No Response Timeout"),
        ],
        string="Trigger",
        required=True,
        default="keyword",
    )
    
    # Keyword matching
    keywords = fields.Text(
        string="Keywords",
        help="One keyword per line (case insensitive)",
    )
    keyword_match_type = fields.Selection(
        [
            ("exact", "Exact Match"),
            ("contains", "Contains"),
            ("starts_with", "Starts With"),
            ("regex", "Regular Expression"),
        ],
        default="contains",
    )
    
    # Button/List matching
    button_id = fields.Char(
        string="Button ID",
        help="Match specific button ID from interactive message",
    )
    
    # Timeout (for no_response trigger)
    timeout_hours = fields.Float(
        string="Timeout (hours)",
        default=24,
        help="Hours without response before triggering",
    )
    
    # Actions
    action_type = fields.Selection(
        [
            ("send_message", "Send Message"),
            ("send_template", "Send Template"),
            ("send_interactive", "Send Interactive"),
            ("send_flow", "Send Flow"),
            ("create_lead", "Create Lead"),
            ("create_ticket", "Create Ticket"),
            ("assign_agent", "Assign to Agent"),
            ("add_tag", "Add Tag"),
            ("execute_code", "Execute Python Code"),
        ],
        string="Action",
        required=True,
        default="send_message",
    )
    
    # Action: Send message
    response_message = fields.Text(
        string="Response Message",
        help="Supports placeholders: {name}, {phone}",
    )
    
    # Action: Send template
    template_id = fields.Many2one(
        "mail.whatsapp.template",
        string="Template",
    )
    
    # Action: Send interactive
    interactive_id = fields.Many2one(
        "mail.whatsapp.interactive",
        string="Interactive Message",
    )
    
    # Action: Send flow
    flow_id = fields.Many2one(
        "mail.whatsapp.flow",
        string="Flow",
    )
    
    # Action: Create lead
    lead_team_id = fields.Many2one(
        "crm.team",
        string="Sales Team",
    )
    lead_user_id = fields.Many2one(
        "res.users",
        string="Salesperson",
    )
    
    # Action: Assign agent
    agent_id = fields.Many2one(
        "res.users",
        string="Assign To",
    )
    agent_queue_id = fields.Many2one(
        "mail.whatsapp.queue",
        string="Assign to Queue",
    )
    
    # Action: Execute code
    python_code = fields.Text(
        string="Python Code",
        help="Variables: channel, message, partner, env",
    )
    
    # Conditions
    only_first_message = fields.Boolean(
        string="Only First Message",
        help="Only trigger on first message in conversation",
    )
    business_hours_only = fields.Boolean(
        string="Business Hours Only",
        help="Only trigger during business hours",
    )
    
    # Stats
    trigger_count = fields.Integer(
        string="Times Triggered",
        readonly=True,
    )
    last_triggered = fields.Datetime(
        string="Last Triggered",
        readonly=True,
    )

    def check_trigger(self, channel, message_text, event_type, event_data=None):
        """
        Check if this automation should be triggered.
        
        Args:
            channel: mail.channel record
            message_text: The message text received
            event_type: Type of event (message, button_click, etc.)
            event_data: Additional event data
            
        Returns:
            bool: True if automation should trigger
        """
        self.ensure_one()
        
        # Check trigger type matches event
        if self.trigger_type == "new_conversation":
            # Check if this is first message
            message_count = self.env["mail.message"].search_count([
                ("res_id", "=", channel.id),
                ("model", "=", "mail.channel"),
            ])
            if message_count > 1:
                return False
                
        elif self.trigger_type == "keyword":
            if not self._match_keywords(message_text):
                return False
                
        elif self.trigger_type == "button_click":
            if event_type != "button_click":
                return False
            if self.button_id and event_data.get("button_id") != self.button_id:
                return False
                
        elif self.trigger_type == "message_received":
            if event_type not in ("message", "message_received"):
                return False
        
        # Check additional conditions
        if self.only_first_message:
            message_count = self.env["mail.message"].search_count([
                ("res_id", "=", channel.id),
                ("model", "=", "mail.channel"),
            ])
            if message_count > 1:
                return False
        
        return True

    def _match_keywords(self, text):
        """Check if text matches configured keywords"""
        if not self.keywords or not text:
            return False
        
        text = text.lower().strip()
        keywords = [k.strip().lower() for k in self.keywords.split("\n") if k.strip()]
        
        for keyword in keywords:
            if self.keyword_match_type == "exact":
                if text == keyword:
                    return True
            elif self.keyword_match_type == "contains":
                if keyword in text:
                    return True
            elif self.keyword_match_type == "starts_with":
                if text.startswith(keyword):
                    return True
            elif self.keyword_match_type == "regex":
                try:
                    if re.search(keyword, text, re.IGNORECASE):
                        return True
                except re.error:
                    pass
        
        return False

    def execute_action(self, channel, message_text=None, partner=None):
        """
        Execute the automation action.
        
        Args:
            channel: mail.channel record
            message_text: Original message
            partner: res.partner if known
        """
        self.ensure_one()
        
        # Update stats
        self.write({
            "trigger_count": self.trigger_count + 1,
            "last_triggered": fields.Datetime.now(),
        })
        
        gateway = channel.gateway_id or self.gateway_id
        phone = channel.whatsapp_number if hasattr(channel, 'whatsapp_number') else None
        
        try:
            if self.action_type == "send_message":
                self._action_send_message(gateway, phone, partner)
                
            elif self.action_type == "send_template":
                self._action_send_template(gateway, phone)
                
            elif self.action_type == "send_interactive":
                self._action_send_interactive(phone)
                
            elif self.action_type == "send_flow":
                self._action_send_flow(phone)
                
            elif self.action_type == "create_lead":
                self._action_create_lead(channel, partner, message_text)
                
            elif self.action_type == "assign_agent":
                self._action_assign_agent(channel)
                
            elif self.action_type == "execute_code":
                self._action_execute_code(channel, message_text, partner)
            
            _logger.info("Automation '%s' executed successfully", self.name)
            
        except Exception as e:
            _logger.error("Automation '%s' failed: %s", self.name, e)

    def _action_send_message(self, gateway, phone, partner):
        """Send a text message"""
        if not phone or not self.response_message:
            return
        
        # Replace placeholders
        message = self.response_message
        if partner:
            message = message.replace("{name}", partner.name or "")
        message = message.replace("{phone}", phone or "")
        
        WhatsApp = self.env["mail.gateway.whatsapp"]
        WhatsApp._send_text_message(gateway, phone, message)

    def _action_send_template(self, gateway, phone):
        """Send a template message"""
        if not phone or not self.template_id:
            return
        
        WhatsApp = self.env["mail.gateway.whatsapp"]
        WhatsApp._send_template_message(gateway, phone, self.template_id, {})

    def _action_send_interactive(self, phone):
        """Send an interactive message"""
        if not phone or not self.interactive_id:
            return
        
        self.interactive_id.send_interactive_message(phone)

    def _action_send_flow(self, phone):
        """Send a flow message"""
        if not phone or not self.flow_id:
            return
        
        self.flow_id.send_flow_message(phone)

    def _action_create_lead(self, channel, partner, message_text):
        """Create a CRM lead"""
        vals = {
            "name": f"WhatsApp: {partner.name if partner else 'New Contact'}",
            "partner_id": partner.id if partner else False,
            "description": message_text or "",
            "type": "lead",
        }
        
        if self.lead_team_id:
            vals["team_id"] = self.lead_team_id.id
        if self.lead_user_id:
            vals["user_id"] = self.lead_user_id.id
        
        lead = self.env["crm.lead"].sudo().create(vals)
        
        _logger.info("Created lead %s from automation", lead.id)
        return lead

    def _action_assign_agent(self, channel):
        """Assign conversation to an agent or queue"""
        if self.agent_id:
            channel.write({"whatsapp_assigned_user_id": self.agent_id.id})
        elif self.agent_queue_id:
            self.agent_queue_id.assign_conversation(channel)

    def _action_execute_code(self, channel, message_text, partner):
        """Execute custom Python code"""
        if not self.python_code:
            return
        
        # Safe execution context
        local_vars = {
            "channel": channel,
            "message": message_text,
            "partner": partner,
            "env": self.env,
            "result": None,
        }
        
        try:
            exec(self.python_code, {"__builtins__": {}}, local_vars)  # noqa: S102
        except Exception as e:
            _logger.error("Automation code execution failed: %s", e)

    @api.model
    def process_incoming_message(self, channel, message_text, event_type="message", event_data=None):
        """
        Process incoming message against all active automations.
        
        Called from webhook processing.
        """
        automations = self.search([
            ("active", "=", True),
            "|",
            ("gateway_id", "=", False),
            ("gateway_id", "=", channel.gateway_id.id),
        ], order="sequence")
        
        partner = channel.partner_id if channel else None
        
        for automation in automations:
            if automation.check_trigger(channel, message_text, event_type, event_data):
                automation.execute_action(channel, message_text, partner)
                # Only execute first matching automation
                break
