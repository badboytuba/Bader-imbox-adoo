# Copyright 2024 Modernized by OCA Contributors
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import json
import logging

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MailWhatsAppChatbot(models.Model):
    """
    AI-powered WhatsApp Chatbot.
    
    Provides automated responses using OpenAI/Claude APIs
    with human handoff capability.
    """
    _name = "mail.whatsapp.chatbot"
    _description = "WhatsApp AI Chatbot"
    _order = "name"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    
    gateway_ids = fields.Many2many(
        "mail.gateway",
        string="Gateways",
        domain=[("gateway_type", "=", "whatsapp")],
    )
    
    # AI Provider
    provider = fields.Selection(
        [
            ("openai", "OpenAI (GPT)"),
            ("claude", "Anthropic (Claude)"),
            ("custom", "Custom API"),
        ],
        default="openai",
        required=True,
    )
    api_key = fields.Char(
        string="API Key",
        help="API key for the AI provider",
    )
    model_name = fields.Char(
        string="Model",
        default="gpt-4o-mini",
        help="Model to use (e.g., gpt-4o-mini, claude-3-sonnet)",
    )
    api_endpoint = fields.Char(
        string="API Endpoint",
        help="Custom API endpoint (for custom provider)",
    )
    
    # Behavior
    system_prompt = fields.Text(
        string="System Prompt",
        default="""Você é um assistente virtual de atendimento ao cliente.
Seja educado, prestativo e conciso.
Se não souber a resposta, diga que vai transferir para um atendente humano.
Responda sempre em português.""",
    )
    max_tokens = fields.Integer(
        string="Max Response Tokens",
        default=500,
    )
    temperature = fields.Float(
        string="Temperature",
        default=0.7,
        help="0 = deterministic, 1 = creative",
    )
    
    # Context
    include_conversation_history = fields.Boolean(
        string="Include Conversation History",
        default=True,
        help="Send previous messages as context",
    )
    max_history_messages = fields.Integer(
        string="Max History Messages",
        default=10,
    )
    
    # Knowledge base
    knowledge_base = fields.Text(
        string="Knowledge Base",
        help="Information the bot should know about your business",
    )
    
    # Handoff
    enable_handoff = fields.Boolean(
        string="Enable Human Handoff",
        default=True,
    )
    handoff_keywords = fields.Text(
        string="Handoff Keywords",
        default="humano\natendente\npessoa\nfalar com alguém",
        help="One keyword per line that triggers handoff",
    )
    handoff_queue_id = fields.Many2one(
        "mail.whatsapp.queue",
        string="Handoff Queue",
        help="Queue to assign when human is requested",
    )
    handoff_message = fields.Text(
        string="Handoff Message",
        default="Vou transferir você para um de nossos atendentes. Por favor, aguarde um momento.",
    )
    
    # Working hours
    active_outside_hours = fields.Boolean(
        string="Active Outside Business Hours",
        default=True,
    )
    outside_hours_message = fields.Text(
        string="Outside Hours Message",
        default="Nosso atendimento funciona de segunda a sexta, das 9h às 18h. Deixe sua mensagem que retornaremos em breve.",
    )
    
    # Stats
    messages_handled = fields.Integer(
        string="Messages Handled",
        readonly=True,
    )
    handoffs_triggered = fields.Integer(
        string="Handoffs Triggered",
        readonly=True,
    )

    def process_message(self, channel, message_text, partner=None):
        """
        Process incoming message and generate AI response.
        
        Args:
            channel: mail.channel record
            message_text: The customer's message
            partner: res.partner if known
            
        Returns:
            str: Bot response or None if handoff
        """
        self.ensure_one()
        
        # Check for handoff keywords
        if self.enable_handoff and self._should_handoff(message_text):
            self._trigger_handoff(channel)
            return self.handoff_message
        
        # Build conversation context
        messages = self._build_messages(channel, message_text, partner)
        
        # Get AI response
        try:
            if self.provider == "openai":
                response = self._call_openai(messages)
            elif self.provider == "claude":
                response = self._call_claude(messages)
            else:
                response = self._call_custom(messages)
            
            # Update stats
            self.write({"messages_handled": self.messages_handled + 1})
            
            return response
            
        except Exception as e:
            _logger.error("Chatbot error: %s", e)
            return "Desculpe, ocorreu um erro. Por favor, tente novamente."

    def _should_handoff(self, message_text):
        """Check if message triggers human handoff"""
        if not self.handoff_keywords:
            return False
        
        message_lower = message_text.lower()
        keywords = [k.strip().lower() for k in self.handoff_keywords.split("\n") if k.strip()]
        
        return any(kw in message_lower for kw in keywords)

    def _trigger_handoff(self, channel):
        """Transfer conversation to human agent"""
        self.write({"handoffs_triggered": self.handoffs_triggered + 1})
        
        if self.handoff_queue_id:
            self.handoff_queue_id.assign_conversation(channel)
        
        # Mark channel as needing human attention
        if hasattr(channel, 'whatsapp_needs_human'):
            channel.write({"whatsapp_needs_human": True})

    def _build_messages(self, channel, current_message, partner=None):
        """Build message history for AI context"""
        messages = []
        
        # System prompt with knowledge base
        system_content = self.system_prompt or ""
        if self.knowledge_base:
            system_content += f"\n\nInformações sobre a empresa:\n{self.knowledge_base}"
        if partner:
            system_content += f"\n\nCliente: {partner.name}"
        
        messages.append({
            "role": "system",
            "content": system_content,
        })
        
        # Conversation history
        if self.include_conversation_history:
            history = self.env["mail.message"].search([
                ("res_id", "=", channel.id),
                ("model", "=", "mail.channel"),
                ("body", "!=", False),
                ("body", "!=", ""),
            ], order="create_date desc", limit=self.max_history_messages)
            
            for msg in reversed(history):
                # Determine role based on author
                role = "assistant" if msg.author_id == self.env.user.partner_id else "user"
                
                # Clean HTML from body
                import re
                body = re.sub(r'<[^>]+>', '', msg.body or "")
                
                if body.strip():
                    messages.append({
                        "role": role,
                        "content": body.strip(),
                    })
        
        # Current message
        messages.append({
            "role": "user",
            "content": current_message,
        })
        
        return messages

    def _call_openai(self, messages):
        """Call OpenAI API"""
        if not self.api_key:
            raise UserError(_("OpenAI API key is required"))
        
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model_name or "gpt-4o-mini",
                "messages": messages,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
            },
            timeout=30,
        )
        response.raise_for_status()
        
        result = response.json()
        return result["choices"][0]["message"]["content"]

    def _call_claude(self, messages):
        """Call Anthropic Claude API"""
        if not self.api_key:
            raise UserError(_("Anthropic API key is required"))
        
        # Convert messages format for Claude
        system = ""
        claude_messages = []
        
        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                claude_messages.append(msg)
        
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": self.model_name or "claude-3-sonnet-20240229",
                "max_tokens": self.max_tokens,
                "system": system,
                "messages": claude_messages,
            },
            timeout=30,
        )
        response.raise_for_status()
        
        result = response.json()
        return result["content"][0]["text"]

    def _call_custom(self, messages):
        """Call custom API endpoint"""
        if not self.api_endpoint:
            raise UserError(_("Custom API endpoint is required"))
        
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        
        response = requests.post(
            self.api_endpoint,
            headers=headers,
            json={"messages": messages},
            timeout=30,
        )
        response.raise_for_status()
        
        result = response.json()
        return result.get("response") or result.get("content") or str(result)

    @api.model
    def get_active_for_gateway(self, gateway_id):
        """Get active chatbot for a gateway"""
        return self.search([
            ("active", "=", True),
            ("gateway_ids", "in", [gateway_id]),
        ], limit=1)
