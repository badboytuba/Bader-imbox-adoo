# Copyright 2024 Modernized by OCA Contributors
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class MailWhatsAppTemplateVariable(models.Model):
    """Variables for WhatsApp message templates ({{1}}, {{2}}, etc.)"""
    _name = "mail.whatsapp.template.variable"
    _description = "WhatsApp Template Variable"
    _order = "position"
    _rec_name = "display_name"

    template_id = fields.Many2one(
        "mail.whatsapp.template",
        string="Template",
        required=True,
        ondelete="cascade",
        index=True,
    )
    position = fields.Integer(
        string="Position",
        required=True,
        help="The variable number (1 for {{1}}, 2 for {{2}}, etc.)",
    )
    name = fields.Char(
        string="Name",
        help="Descriptive name for this variable (e.g., 'Customer Name')",
    )
    field_name = fields.Char(
        string="Field Name",
        help="Odoo field name to use for this variable (e.g., 'partner_id.name')",
    )
    model_id = fields.Many2one(
        "ir.model",
        string="Model",
        help="Model to get the field value from",
    )
    sample_value = fields.Char(
        string="Sample Value",
        help="Sample value for preview purposes",
    )
    default_value = fields.Char(
        string="Default Value",
        help="Default value if the field is empty",
    )
    display_name = fields.Char(
        compute="_compute_display_name",
        store=True,
    )

    @api.depends("position", "name")
    def _compute_display_name(self):
        for record in self:
            record.display_name = f"{{{{{{record.position}}}}}} - {record.name or 'Variable'}"

    def get_value(self, record):
        """Get the actual value for this variable from a record"""
        self.ensure_one()
        
        if not self.field_name or not record:
            return self.default_value or ""
        
        try:
            # Navigate through field path (e.g., 'partner_id.name')
            value = record
            for field_part in self.field_name.split('.'):
                if hasattr(value, field_part):
                    value = getattr(value, field_part)
                else:
                    return self.default_value or ""
            return str(value) if value else (self.default_value or "")
        except Exception:
            return self.default_value or ""


class MailWhatsAppTemplateButton(models.Model):
    """Buttons for WhatsApp message templates"""
    _name = "mail.whatsapp.template.button"
    _description = "WhatsApp Template Button"
    _order = "sequence, id"
    _rec_name = "text"

    template_id = fields.Many2one(
        "mail.whatsapp.template",
        string="Template",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    button_type = fields.Selection(
        [
            ("quick_reply", "Quick Reply"),
            ("url", "URL"),
            ("phone_number", "Phone Number"),
            ("copy_code", "Copy Code"),
        ],
        string="Type",
        required=True,
        default="quick_reply",
    )
    text = fields.Char(
        string="Button Text",
        required=True,
        help="Text displayed on the button (max 25 characters)",
    )
    
    # URL Button fields
    url = fields.Char(
        string="URL",
        help="URL to open when button is clicked. Can contain {{1}} for dynamic URLs.",
    )
    url_type = fields.Selection(
        [
            ("static", "Static"),
            ("dynamic", "Dynamic (with variable)"),
        ],
        string="URL Type",
        default="static",
    )
    
    # Phone Button fields
    phone_number = fields.Char(
        string="Phone Number",
        help="Phone number to call (with country code, e.g., +5511999999999)",
    )
    
    # Copy Code fields
    copy_code_example = fields.Char(
        string="Example Code",
        help="Example code for preview purposes",
    )

    def _prepare_export_data(self):
        """Prepare button data for WhatsApp API export"""
        self.ensure_one()
        
        if self.button_type == "quick_reply":
            return {
                "type": "QUICK_REPLY",
                "text": self.text,
            }
        elif self.button_type == "url":
            data = {
                "type": "URL",
                "text": self.text,
                "url": self.url,
            }
            if self.url_type == "dynamic":
                data["example"] = [self.url]
            return data
        elif self.button_type == "phone_number":
            return {
                "type": "PHONE_NUMBER",
                "text": self.text,
                "phone_number": self.phone_number,
            }
        elif self.button_type == "copy_code":
            return {
                "type": "COPY_CODE",
                "example": self.copy_code_example or "CODE123",
            }
        
        return {}

    def _prepare_send_data(self, variables=None):
        """Prepare button data for sending a message"""
        self.ensure_one()
        variables = variables or {}
        
        if self.button_type == "quick_reply":
            # Quick reply buttons don't need parameters for sending
            return {}
        elif self.button_type == "url" and self.url_type == "dynamic":
            # Dynamic URL needs the variable value
            return {
                "type": "button",
                "sub_type": "url",
                "index": str(self.sequence - 1),
                "parameters": [
                    {"type": "text", "text": variables.get(1, "")}
                ]
            }
        
        return {}
