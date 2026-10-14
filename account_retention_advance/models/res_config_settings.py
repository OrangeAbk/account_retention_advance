from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    retention_receivable_account_id = fields.Many2one(
        "account.account", string='Retention Receivable Account',related='company_id.retention_receivable_account_id',readonly=False, )

    retention_percentage = fields.Float( "Default Retention Percentage",related='company_id.default_retention_percentage', readonly=False, digits=(16, 2),)
    advance_liability_account_id = fields.Many2one( related="company_id.advance_liability_account_id",readonly=False, string="Advance Liability Account")

