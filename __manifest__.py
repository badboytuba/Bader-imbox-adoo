# Copyright 2022 Creu Blanca
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

{
    "name": "Bader Inbox",
    "summary": """
        WhatsApp gateway with Cloud API & Evolution API (QR Code), 
        AI chatbot, flows, campaigns, multi-agent, and analytics""",
    "version": "16.0.3.0.0",
    "license": "AGPL-3",
    "author": "Bader Business, Creu Blanca, Odoo Community Association (OCA)",
    "website": "https://github.com/badboytuba/Bader-imbox-adoo",

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
            "bader_inbox/static/src/models/**/*.js",
        ],
        "web.assets_backend": [
            "bader_inbox/static/src/components/**/*.xml",
            "bader_inbox/static/src/components/**/*.js",
        ],
    },
}
