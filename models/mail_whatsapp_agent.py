# Copyright 2024 Modernized by OCA Contributors
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class MailWhatsAppQueue(models.Model):
    """
    Agent queues for WhatsApp conversations.
    
    Manages conversation distribution among agents.
    """
    _name = "mail.whatsapp.queue"
    _description = "WhatsApp Agent Queue"
    _order = "sequence, name"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    
    gateway_ids = fields.Many2many(
        "mail.gateway",
        string="Gateways",
        domain=[("gateway_type", "=", "whatsapp")],
    )
    
    # Agent assignment
    agent_ids = fields.Many2many(
        "res.users",
        string="Agents",
        domain=[("share", "=", False)],
    )
    assignment_method = fields.Selection(
        [
            ("round_robin", "Round Robin"),
            ("least_busy", "Least Busy"),
            ("manual", "Manual Assignment"),
            ("random", "Random"),
        ],
        default="round_robin",
        required=True,
    )
    last_assigned_agent_id = fields.Many2one(
        "res.users",
        string="Last Assigned",
        help="Used for round-robin assignment",
    )
    
    # Limits
    max_conversations_per_agent = fields.Integer(
        string="Max Conversations/Agent",
        default=20,
        help="Maximum active conversations per agent (0 = unlimited)",
    )
    
    # Auto-assignment
    auto_assign_new = fields.Boolean(
        string="Auto-assign New Conversations",
        default=True,
    )
    
    # Stats
    waiting_count = fields.Integer(
        compute="_compute_stats",
        string="Waiting",
    )
    active_count = fields.Integer(
        compute="_compute_stats",
        string="Active",
    )

    def _compute_stats(self):
        Assignment = self.env["mail.whatsapp.assignment"]
        for queue in self:
            queue.waiting_count = Assignment.search_count([
                ("queue_id", "=", queue.id),
                ("state", "=", "waiting"),
            ])
            queue.active_count = Assignment.search_count([
                ("queue_id", "=", queue.id),
                ("state", "=", "active"),
            ])

    def assign_conversation(self, channel):
        """
        Assign a conversation to an agent from this queue.
        
        Returns:
            res.users: Assigned agent or False
        """
        self.ensure_one()
        
        # Get available agents
        available_agents = self._get_available_agents()
        
        if not available_agents:
            # No available agents - add to waiting
            self._create_waiting_assignment(channel)
            return False
        
        # Select agent based on method
        if self.assignment_method == "round_robin":
            agent = self._round_robin_select(available_agents)
        elif self.assignment_method == "least_busy":
            agent = self._least_busy_select(available_agents)
        elif self.assignment_method == "random":
            import random
            agent = random.choice(available_agents)
        else:
            # Manual - create waiting assignment
            self._create_waiting_assignment(channel)
            return False
        
        # Create assignment
        self._create_assignment(channel, agent)
        
        return agent

    def _get_available_agents(self):
        """Get list of agents who can accept new conversations"""
        Assignment = self.env["mail.whatsapp.assignment"]
        available = []
        
        for agent in self.agent_ids:
            # Check if agent is available (online/active)
            if not self._is_agent_available(agent):
                continue
            
            # Check max conversations limit
            if self.max_conversations_per_agent > 0:
                active_count = Assignment.search_count([
                    ("agent_id", "=", agent.id),
                    ("state", "=", "active"),
                ])
                if active_count >= self.max_conversations_per_agent:
                    continue
            
            available.append(agent)
        
        return available

    def _is_agent_available(self, agent):
        """Check if agent is currently available"""
        # Check agent status if available
        AgentStatus = self.env.get("mail.whatsapp.agent.status")
        if AgentStatus:
            status = AgentStatus.search([
                ("user_id", "=", agent.id),
            ], limit=1)
            if status and status.status == "offline":
                return False
        return True

    def _round_robin_select(self, agents):
        """Select next agent in round-robin fashion"""
        if not agents:
            return False
        
        # Find position of last assigned agent
        last_idx = -1
        if self.last_assigned_agent_id:
            for i, agent in enumerate(agents):
                if agent.id == self.last_assigned_agent_id.id:
                    last_idx = i
                    break
        
        # Select next agent
        next_idx = (last_idx + 1) % len(agents)
        selected = agents[next_idx]
        
        # Update last assigned
        self.write({"last_assigned_agent_id": selected.id})
        
        return selected

    def _least_busy_select(self, agents):
        """Select agent with fewest active conversations"""
        Assignment = self.env["mail.whatsapp.assignment"]
        
        min_count = float("inf")
        selected = agents[0] if agents else False
        
        for agent in agents:
            count = Assignment.search_count([
                ("agent_id", "=", agent.id),
                ("state", "=", "active"),
            ])
            if count < min_count:
                min_count = count
                selected = agent
        
        return selected

    def _create_assignment(self, channel, agent):
        """Create an active assignment"""
        return self.env["mail.whatsapp.assignment"].create({
            "queue_id": self.id,
            "channel_id": channel.id,
            "agent_id": agent.id,
            "state": "active",
            "assigned_at": fields.Datetime.now(),
        })

    def _create_waiting_assignment(self, channel):
        """Create a waiting assignment (no agent yet)"""
        return self.env["mail.whatsapp.assignment"].create({
            "queue_id": self.id,
            "channel_id": channel.id,
            "state": "waiting",
        })


class MailWhatsAppAssignment(models.Model):
    """Track agent assignments to WhatsApp conversations"""
    _name = "mail.whatsapp.assignment"
    _description = "WhatsApp Conversation Assignment"
    _order = "create_date desc"

    queue_id = fields.Many2one(
        "mail.whatsapp.queue",
        string="Queue",
        ondelete="cascade",
    )
    channel_id = fields.Many2one(
        "mail.channel",
        string="Conversation",
        required=True,
        ondelete="cascade",
    )
    agent_id = fields.Many2one(
        "res.users",
        string="Agent",
        ondelete="set null",
    )
    state = fields.Selection(
        [
            ("waiting", "Waiting"),
            ("active", "Active"),
            ("resolved", "Resolved"),
            ("transferred", "Transferred"),
        ],
        default="waiting",
        required=True,
        index=True,
    )
    
    # Timestamps
    assigned_at = fields.Datetime(string="Assigned At")
    first_response_at = fields.Datetime(string="First Response At")
    resolved_at = fields.Datetime(string="Resolved At")
    
    # Metrics
    response_time_seconds = fields.Integer(
        compute="_compute_metrics",
        string="Response Time (s)",
        store=True,
    )
    resolution_time_seconds = fields.Integer(
        compute="_compute_metrics",
        string="Resolution Time (s)",
        store=True,
    )
    
    # Notes
    notes = fields.Text(string="Notes")
    resolution_type = fields.Selection(
        [
            ("resolved", "Resolved"),
            ("no_response", "No Response Needed"),
            ("transferred", "Transferred"),
            ("spam", "Spam/Irrelevant"),
        ],
        string="Resolution Type",
    )

    @api.depends("assigned_at", "first_response_at", "resolved_at")
    def _compute_metrics(self):
        for record in self:
            if record.assigned_at and record.first_response_at:
                delta = record.first_response_at - record.assigned_at
                record.response_time_seconds = int(delta.total_seconds())
            else:
                record.response_time_seconds = 0
            
            if record.assigned_at and record.resolved_at:
                delta = record.resolved_at - record.assigned_at
                record.resolution_time_seconds = int(delta.total_seconds())
            else:
                record.resolution_time_seconds = 0

    def action_assign_to_me(self):
        """Assign conversation to current user"""
        self.ensure_one()
        self.write({
            "agent_id": self.env.user.id,
            "state": "active",
            "assigned_at": fields.Datetime.now(),
        })

    def action_transfer(self, target_agent=None, target_queue=None):
        """Transfer to another agent or queue"""
        self.ensure_one()
        
        self.write({
            "state": "transferred",
            "resolved_at": fields.Datetime.now(),
        })
        
        if target_agent:
            self.env["mail.whatsapp.assignment"].create({
                "channel_id": self.channel_id.id,
                "agent_id": target_agent.id,
                "state": "active",
                "assigned_at": fields.Datetime.now(),
            })
        elif target_queue:
            target_queue.assign_conversation(self.channel_id)

    def action_resolve(self, resolution_type="resolved"):
        """Mark conversation as resolved"""
        self.ensure_one()
        self.write({
            "state": "resolved",
            "resolved_at": fields.Datetime.now(),
            "resolution_type": resolution_type,
        })


class MailWhatsAppAgentStatus(models.Model):
    """Track agent availability status"""
    _name = "mail.whatsapp.agent.status"
    _description = "WhatsApp Agent Status"
    _rec_name = "user_id"

    user_id = fields.Many2one(
        "res.users",
        string="Agent",
        required=True,
        ondelete="cascade",
        index=True,
    )
    status = fields.Selection(
        [
            ("online", "Online"),
            ("away", "Away"),
            ("busy", "Busy"),
            ("offline", "Offline"),
        ],
        default="offline",
        required=True,
    )
    status_message = fields.Char(string="Status Message")
    last_activity = fields.Datetime(
        string="Last Activity",
        default=fields.Datetime.now,
    )
    
    # Auto-offline
    auto_offline_minutes = fields.Integer(
        string="Auto-offline (min)",
        default=30,
        help="Set to offline after this many minutes of inactivity",
    )

    _sql_constraints = [
        ("user_unique", "unique(user_id)", "Each user can only have one status record"),
    ]

    def action_go_online(self):
        self.write({
            "status": "online",
            "last_activity": fields.Datetime.now(),
        })

    def action_go_offline(self):
        self.write({"status": "offline"})

    @api.model
    def update_activity(self, user_id=None):
        """Update last activity timestamp"""
        user_id = user_id or self.env.user.id
        status = self.search([("user_id", "=", user_id)], limit=1)
        if status:
            status.write({"last_activity": fields.Datetime.now()})
        else:
            self.create({
                "user_id": user_id,
                "status": "online",
            })

    @api.model
    def _cron_auto_offline(self):
        """Set inactive agents to offline"""
        now = fields.Datetime.now()
        
        statuses = self.search([
            ("status", "!=", "offline"),
            ("auto_offline_minutes", ">", 0),
        ])
        
        for status in statuses:
            if status.last_activity:
                inactive_time = now - status.last_activity
                if inactive_time > timedelta(minutes=status.auto_offline_minutes):
                    status.write({"status": "offline"})
                    _logger.info("Agent %s set to offline (inactive)", status.user_id.name)
