from odoo import api, fields, models

class ResCompany(models.Model):
    _inherit = "res.company"

    retention_receivable_account_id = fields.Many2one(
        "account.account",string=r'Retention Receivable Account', check_company=True,
    )
    default_retention_percentage = fields.Float("Default Retention Percentage",default=10.0,digits=(16,2))
    advance_liability_account_id = fields.Many2one(
        "account.account",
        string="Advance Liability Account",
        domain=[
            ("account_type", "=", "liability_current"),],check_company=True )
