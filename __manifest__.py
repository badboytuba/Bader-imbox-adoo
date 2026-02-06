# Copyright 2022 Creu Blanca
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

{
    "name": "Mail Whatsapp Gateway",
    "summary": """
        Set a gateway for whatsapp with modern API support, AI chatbot, 
        flows, campaigns, multi-agent, and analytics""",
    "version": "16.0.3.0.0",
    "license": "AGPL-3",
    "author": "Creu Blanca, Dixmit, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/social",
    "depends": ["mail_gateway", "phone_validation", "crm"],
    "external_dependencies": {"python": ["requests_toolbelt", "requests"]},
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/mail_whatsapp_cron.xml",
        "wizards/whatsapp_composer.xml",
        "wizards/mail_compose_gateway_message.xml",
        "views/mail_whatsapp_template_views.xml",
        "views/mail_gateway.xml",
        "views/mail_whatsapp_advanced_views.xml",
        "views/mail_whatsapp_agent_views.xml",
        "views/mail_gateway_evolution_views.xml",
        "views/mail_whatsapp_menus.xml",
    ],
    "assets": {
        "mail.assets_messaging": [
            "mail_gateway_whatsapp/static/src/models/**/*.js",
        ],
        "web.assets_backend": [
            "mail_gateway_whatsapp/static/src/components/**/*.xml",
            "mail_gateway_whatsapp/static/src/components/**/*.js",
        ],
    },
}
