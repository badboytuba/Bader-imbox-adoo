# Copyright 2024 Modernized by OCA Contributors
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import json
import logging

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MailWhatsAppFlow(models.Model):
    """
    WhatsApp Flows - Interactive forms within WhatsApp chat.
    
    Flows allow building multi-screen forms that customers can fill
    without leaving the WhatsApp conversation.
    """
    _name = "mail.whatsapp.flow"
    _description = "WhatsApp Flow"
    _order = "name"
    _rec_name = "name"

    name = fields.Char(required=True, string="Flow Name")
    gateway_id = fields.Many2one(
        "mail.gateway",
        string="Gateway",
        required=True,
        domain=[("gateway_type", "=", "whatsapp")],
        ondelete="cascade",
    )
    flow_id = fields.Char(
        string="Meta Flow ID",
        readonly=True,
        help="Flow ID assigned by Meta after deployment",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("published", "Published"),
            ("deprecated", "Deprecated"),
            ("blocked", "Blocked"),
        ],
        default="draft",
        required=True,
    )
    category = fields.Selection(
        [
            ("lead_generation", "Lead Generation"),
            ("customer_support", "Customer Support"),
            ("appointment", "Appointment Booking"),
            ("survey", "Survey/Feedback"),
            ("order", "Order Form"),
            ("custom", "Custom"),
        ],
        default="custom",
        required=True,
    )
    description = fields.Text(string="Description")
    
    # Flow screens
    screen_ids = fields.One2many(
        "mail.whatsapp.flow.screen",
        "flow_id",
        string="Screens",
    )
    
    # Integration with Odoo
    target_model = fields.Selection(
        [
            ("crm.lead", "CRM Lead"),
            ("res.partner", "Contact"),
            ("sale.order", "Sales Order"),
            ("helpdesk.ticket", "Helpdesk Ticket"),
            ("project.task", "Project Task"),
            ("custom", "Custom Action"),
        ],
        string="Create Record In",
        default="crm.lead",
        help="When flow is completed, create a record in this model",
    )
    field_mapping_ids = fields.One2many(
        "mail.whatsapp.flow.mapping",
        "flow_id",
        string="Field Mappings",
    )
    
    # CTA Button
    cta_text = fields.Char(
        string="Button Text",
        default="Iniciar",
        help="Text for the button that opens the flow",
    )
    header_text = fields.Char(string="Header")
    body_text = fields.Text(
        string="Body Message",
        help="Message shown before the flow button",
    )
    footer_text = fields.Char(string="Footer")

    def action_deploy_flow(self):
        """Deploy flow to Meta WhatsApp Business API"""
        self.ensure_one()
        
        if not self.screen_ids:
            raise UserError(_("Please add at least one screen to the flow"))
        
        gateway = self.gateway_id
        flow_json = self._build_flow_json()
        
        try:
            # Create flow
            url = f"https://graph.facebook.com/v{gateway.whatsapp_version}/{gateway.whatsapp_account_id}/flows"
            
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {gateway.token}"},
                json={
                    "name": self.name,
                    "categories": [self.category.upper()],
                },
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
            
            flow_id = result.get("id")
            self.write({
                "flow_id": flow_id,
                "state": "draft",
            })
            
            # Upload flow JSON
            assets_url = f"https://graph.facebook.com/v{gateway.whatsapp_version}/{flow_id}/assets"
            
            response = requests.post(
                assets_url,
                headers={"Authorization": f"Bearer {gateway.token}"},
                files={
                    "file": ("flow.json", json.dumps(flow_json), "application/json"),
                    "name": (None, "flow.json"),
                    "asset_type": (None, "FLOW_JSON"),
                },
                timeout=30,
            )
            response.raise_for_status()
            
            _logger.info("Flow %s deployed successfully", self.name)
            
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Success"),
                    "message": _("Flow deployed successfully!"),
                    "type": "success",
                }
            }
            
        except requests.exceptions.RequestException as e:
            error_msg = str(e)
            if hasattr(e, 'response') and e.response is not None:
                error_msg = e.response.text
            _logger.error("Failed to deploy flow: %s", error_msg)
            raise UserError(_("Failed to deploy flow: %s") % error_msg) from e

    def action_publish_flow(self):
        """Publish flow to make it available for use"""
        self.ensure_one()
        
        if not self.flow_id:
            raise UserError(_("Please deploy the flow first"))
        
        gateway = self.gateway_id
        
        try:
            url = f"https://graph.facebook.com/v{gateway.whatsapp_version}/{self.flow_id}/publish"
            
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {gateway.token}"},
                timeout=30,
            )
            response.raise_for_status()
            
            self.write({"state": "published"})
            
            _logger.info("Flow %s published successfully", self.name)
            
        except requests.exceptions.RequestException as e:
            _logger.error("Failed to publish flow: %s", e)
            raise UserError(_("Failed to publish flow: %s") % str(e)) from e

    def _build_flow_json(self):
        """Build the Flow JSON structure for Meta API"""
        self.ensure_one()
        
        screens = []
        for screen in self.screen_ids.sorted('sequence'):
            screen_data = {
                "id": screen.screen_id or f"SCREEN_{screen.id}",
                "title": screen.title,
                "data": {},
                "layout": {
                    "type": "SingleColumnLayout",
                    "children": [],
                }
            }
            
            # Add components to screen
            for component in screen.component_ids.sorted('sequence'):
                comp_data = component._build_component_json()
                screen_data["layout"]["children"].append(comp_data)
            
            # Add navigation footer
            if screen.sequence < len(self.screen_ids):
                screen_data["layout"]["children"].append({
                    "type": "Footer",
                    "label": screen.next_button_text or "Continuar",
                    "on-click-action": {
                        "name": "navigate",
                        "next": {"type": "screen", "name": f"SCREEN_{screen.id + 1}"},
                        "payload": {}
                    }
                })
            else:
                # Last screen - complete action
                screen_data["layout"]["children"].append({
                    "type": "Footer",
                    "label": screen.next_button_text or "Enviar",
                    "on-click-action": {
                        "name": "complete",
                        "payload": {}
                    }
                })
            
            screens.append(screen_data)
        
        return {
            "version": "3.1",
            "screens": screens,
        }

    def send_flow_message(self, recipient_phone, header_text=None, body_text=None):
        """Send a message with flow button"""
        self.ensure_one()
        
        if self.state != "published":
            raise UserError(_("Flow must be published before sending"))
        
        gateway = self.gateway_id
        
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_phone.replace("+", "").replace(" ", ""),
            "type": "interactive",
            "interactive": {
                "type": "flow",
                "header": {
                    "type": "text",
                    "text": header_text or self.header_text or self.name,
                },
                "body": {
                    "text": body_text or self.body_text or "Por favor, preencha o formulário.",
                },
                "action": {
                    "name": "flow",
                    "parameters": {
                        "flow_message_version": "3",
                        "flow_id": self.flow_id,
                        "mode": "published",
                        "flow_cta": self.cta_text or "Iniciar",
                        "flow_action": "navigate",
                        "flow_action_payload": {
                            "screen": self.screen_ids[0].screen_id or f"SCREEN_{self.screen_ids[0].id}"
                        }
                    }
                }
            }
        }
        
        if self.footer_text:
            payload["interactive"]["footer"] = {"text": self.footer_text}
        
        try:
            url = f"https://graph.facebook.com/v{gateway.whatsapp_version}/{gateway.whatsapp_from_phone}/messages"
            
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {gateway.token}"},
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            
            _logger.info("Flow message sent to %s", recipient_phone)
            return response.json()
            
        except requests.exceptions.RequestException as e:
            _logger.error("Failed to send flow message: %s", e)
            raise UserError(_("Failed to send flow: %s") % str(e)) from e

    def process_flow_response(self, response_data, channel):
        """
        Process flow response from webhook and create Odoo record.
        
        Called when user completes a flow.
        """
        self.ensure_one()
        
        if self.target_model == "custom":
            # Custom action - just log
            _logger.info("Flow %s completed with data: %s", self.name, response_data)
            return True
        
        # Build values from field mappings
        vals = {}
        for mapping in self.field_mapping_ids:
            flow_value = response_data.get(mapping.flow_field_name)
            if flow_value:
                vals[mapping.odoo_field_name] = flow_value
        
        # Add default values based on target model
        if self.target_model == "crm.lead":
            if channel and channel.partner_id:
                vals.setdefault("partner_id", channel.partner_id.id)
            vals.setdefault("name", f"WhatsApp Flow: {self.name}")
            vals.setdefault("type", "lead")
            vals.setdefault("description", f"Flow response: {json.dumps(response_data, indent=2)}")
        
        elif self.target_model == "res.partner":
            vals.setdefault("name", response_data.get("name", "WhatsApp Contact"))
        
        # Create record
        try:
            Model = self.env[self.target_model].sudo()
            record = Model.create(vals)
            
            _logger.info(
                "Created %s record %s from flow %s",
                self.target_model,
                record.id,
                self.name
            )
            
            # Post message to chatter if available
            if hasattr(record, 'message_post'):
                record.message_post(
                    body=f"Criado automaticamente via WhatsApp Flow: {self.name}",
                    message_type="notification",
                )
            
            return record
            
        except Exception as e:
            _logger.error("Failed to create record from flow: %s", e)
            return False


class MailWhatsAppFlowScreen(models.Model):
    """Screens within a WhatsApp Flow"""
    _name = "mail.whatsapp.flow.screen"
    _description = "WhatsApp Flow Screen"
    _order = "sequence"

    flow_id = fields.Many2one(
        "mail.whatsapp.flow",
        string="Flow",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    screen_id = fields.Char(
        string="Screen ID",
        help="Unique identifier for this screen",
    )
    title = fields.Char(required=True, string="Title")
    component_ids = fields.One2many(
        "mail.whatsapp.flow.component",
        "screen_id",
        string="Components",
    )
    next_button_text = fields.Char(
        default="Continuar",
        string="Button Text",
    )

    @api.model
    def create(self, vals):
        record = super().create(vals)
        if not record.screen_id:
            record.screen_id = f"SCREEN_{record.id}"
        return record


class MailWhatsAppFlowComponent(models.Model):
    """Components within a Flow Screen (inputs, selects, etc.)"""
    _name = "mail.whatsapp.flow.component"
    _description = "WhatsApp Flow Component"
    _order = "sequence"

    screen_id = fields.Many2one(
        "mail.whatsapp.flow.screen",
        string="Screen",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    component_type = fields.Selection(
        [
            ("TextHeading", "Heading"),
            ("TextSubheading", "Subheading"),
            ("TextBody", "Text Body"),
            ("TextInput", "Text Input"),
            ("TextArea", "Text Area"),
            ("Dropdown", "Dropdown"),
            ("RadioButtonsGroup", "Radio Buttons"),
            ("CheckboxGroup", "Checkboxes"),
            ("DatePicker", "Date Picker"),
            ("Image", "Image"),
            ("OptIn", "Opt-in Checkbox"),
        ],
        string="Type",
        required=True,
        default="TextInput",
    )
    name = fields.Char(
        required=True,
        string="Field Name",
        help="Name used to reference this field in responses",
    )
    label = fields.Char(string="Label")
    required = fields.Boolean(default=False)
    helper_text = fields.Char(string="Helper Text")
    
    # For input fields
    input_type = fields.Selection(
        [
            ("text", "Text"),
            ("email", "Email"),
            ("phone", "Phone"),
            ("number", "Number"),
            ("password", "Password"),
        ],
        default="text",
        string="Input Type",
    )
    min_length = fields.Integer(string="Min Length")
    max_length = fields.Integer(string="Max Length")
    
    # For dropdown/radio/checkbox
    option_ids = fields.One2many(
        "mail.whatsapp.flow.option",
        "component_id",
        string="Options",
    )
    
    # For images
    image_url = fields.Char(string="Image URL")
    
    # For text display
    text_content = fields.Text(string="Text Content")

    def _build_component_json(self):
        """Build component JSON for Flow API"""
        self.ensure_one()
        
        comp = {"type": self.component_type}
        
        # Text components
        if self.component_type in ["TextHeading", "TextSubheading", "TextBody"]:
            comp["text"] = self.text_content or self.label or ""
            return comp
        
        # Image
        if self.component_type == "Image":
            comp["src"] = self.image_url
            return comp
        
        # Input components
        comp["name"] = self.name
        if self.label:
            comp["label"] = self.label
        if self.required:
            comp["required"] = True
        if self.helper_text:
            comp["helper-text"] = self.helper_text
        
        # Text input specific
        if self.component_type == "TextInput":
            comp["input-type"] = self.input_type or "text"
            if self.min_length:
                comp["min-chars"] = self.min_length
            if self.max_length:
                comp["max-chars"] = self.max_length
        
        # TextArea specific
        if self.component_type == "TextArea":
            if self.max_length:
                comp["max-chars"] = self.max_length
        
        # Dropdown/Radio/Checkbox - add options
        if self.component_type in ["Dropdown", "RadioButtonsGroup", "CheckboxGroup"]:
            comp["data-source"] = [
                {"id": opt.value, "title": opt.title}
                for opt in self.option_ids
            ]
        
        return comp


class MailWhatsAppFlowOption(models.Model):
    """Options for dropdown/radio/checkbox components"""
    _name = "mail.whatsapp.flow.option"
    _description = "WhatsApp Flow Option"
    _order = "sequence"

    component_id = fields.Many2one(
        "mail.whatsapp.flow.component",
        string="Component",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    value = fields.Char(required=True, string="Value")
    title = fields.Char(required=True, string="Title")


class MailWhatsAppFlowMapping(models.Model):
    """Field mapping between flow fields and Odoo fields"""
    _name = "mail.whatsapp.flow.mapping"
    _description = "WhatsApp Flow Field Mapping"

    flow_id = fields.Many2one(
        "mail.whatsapp.flow",
        string="Flow",
        required=True,
        ondelete="cascade",
    )
    flow_field_name = fields.Char(
        required=True,
        string="Flow Field",
        help="Name of the field in the flow response",
    )
    odoo_field_name = fields.Char(
        required=True,
        string="Odoo Field",
        help="Name of the field in the target Odoo model",
    )
    transform = fields.Selection(
        [
            ("none", "No Transform"),
            ("upper", "Uppercase"),
            ("lower", "Lowercase"),
            ("title", "Title Case"),
        ],
        default="none",
        string="Transform",
    )
