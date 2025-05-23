# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import base64
import codecs
from collections import defaultdict
import csv
import datetime
import io
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError, RedirectWarning
from odoo.tools import float_repr, float_is_zero, get_lang

_logger = logging.getLogger(__name__)


class UnbalancedMovesError(UserError):
    pass


class FecImportWizard(models.TransientModel):
    """ FEC import wizard is the main class to import FEC files.  """

    _name = "account.fec.import.wizard"
    _description = "Account FEC import wizard"

    attachment_name = fields.Char(string="Filename")
    attachment_id = fields.Binary(string="File", required=True, help="Accounting FEC data file to be imported")
    company_id = fields.Many2one(comodel_name="res.company", string="Company", help="Company used for the import", default=lambda self: self.env.company, required=True, readonly=True)

    # ------------------------------------
    # Reading
    # ------------------------------------

    def _get_rows(self, attachment, attachment_name):
        """ Returns the rows that are stored inside the CSV FEC attachment.

            If the file starts with a BOM_UTF8, it's considered utf-8 w/BOM.
            'utf-8' and 'iso8859_15' are the only other allowed encodings.
            Allowed delimiters are ';|,\t', as in the official FEC testing tooling.

            Record fields are stripped of spaces after being read from the file.
        """

        # Determine the encoding
        bytes_data = base64.b64decode(attachment)
        if bytes_data.startswith(codecs.BOM_UTF8):
            string_data = bytes_data.decode('utf-8-sig')
        else:
            for encoding in ['utf-8', 'iso8859_15']:
                try:
                    string_data = bytes_data.decode(encoding)
                    if string_data:
                        break
                except ValueError:
                    pass
            if not string_data:
                raise UserError(_("Cannot determine the encoding for the attached file."))

        # Find the CSV dialect
        try:
            dialect = csv.Sniffer().sniff(string_data, delimiters=";|,\t")
        except csv.Error:
            raise UserError(_("Cannot determine the file format for the attached file."))

        # Return the spaces-stripped rows
        rows = []
        try:
            incomplete_lines = []
            reader = csv.reader(io.StringIO(string_data), dialect=dialect)

            for line_no, record in enumerate(reader, 1):
                # Deal with the header
                if line_no == 1:
                    header = [x.strip() for x in record]
                    while header and not header[-1]:
                        header.pop()
                else:
                    # Read the rows, skipping the empty ones
                    # Then put it in the rows basket, or flag it if it's incomplete
                    row = [x.strip() if isinstance(x, str) else x for x in record]
                    if row:
                        if len(row) >= len(header):
                            rows.append(dict(zip(header, row)))
                        else:
                            incomplete_lines.append(line_no)

        # Print a friendly message to the user
        except csv.Error as e:
            _logger.warning("csv.Error: %s", e)
            raise UserError(_("This file could not be recognised.\n"
                              "Please check that it is encoded in utf-8 or iso8859_15"))

        # Block the processing if there are incomplete lines
        if incomplete_lines:
            raise UserError(_("Some lines do not have the same number of fields as the header.\n"
                              "Please check the following line numbers:\n"
                              "%s", incomplete_lines))

        return rows

    # ------------------------------------
    # Generators
    # -----------------------------------

    def _generator_fec_account_account(self, rows, cache):
        """ Import the accounts from the FEC file """

        accounts_set = set()
        digits = self.company_id.chart_template_id.code_digits
        for record in rows:
            account_code = record.get("CompteNum", "")
            account_name = record.get("CompteLib", "")

            # Should the account appear in many lines, and the first not have the "reconcile" flag
            # then it will eventually be applied by the account template or when reconciling the move_lines.
            # The "reconcile" flag will then be set to True anyway, and this also applies
            # for already existent accounts that will have moves with reconcilable lines
            reconcile = bool(record.get("EcritureLet", ""))

            # The accounts are matched with existing ones by stripping the trailing '0's from their codes.
            if account_code and account_code not in accounts_set:
                accounts_set.add(account_code)
                existing = cache["account.account"].get(account_code.rstrip('0'), None)

                # Shouldn't the account be found, if there are only zeroes after the 6th digit, they are truncated.
                if not existing:
                    account_code = account_code[:digits] + account_code[digits:].rstrip('0')
                    data = {
                        "company_id": self.company_id.id,
                        "code": account_code,
                        "name": account_name,
                        "reconcile": reconcile,
                        "account_type": 'asset_current',
                    }

                    yield data

    def _shorten_code(self, field_name, value, max_len, cache, reserved_digits=2):
        """ In case that given value is too long, this function shortens it like this:

            PACMAN, max_len=5, reserved_digits = 2 -> PAC01
            if PAC01 already exists                -> PAC02
            ...

            If all PACnn exist until PAC99, False is returned.
            If a value is found, or the code isn't too long to begin with,
            then the shortened value is returned.

            If a value is found, a cache entry "mapping_<field_name>"
            is created/updated with the mapping old_value->new_value
        """

        if len(value) <= max_len:
            return value

        mapping_key = "mapping_%s" % field_name
        cache.setdefault(mapping_key, {})

        if value in cache[mapping_key]:
            return cache[mapping_key][value]

        prefix = value[0:(max_len - reserved_digits)]
        for idx in range(1, 10 ** reserved_digits):
            new_value = ("%%s%%0%dd" % reserved_digits) % (prefix, idx)
            if new_value not in cache[mapping_key].values():
                cache[mapping_key][value] = new_value
                return new_value

        return False

    def _generator_fec_account_journal(self, rows, cache):
        """ Import the journals from fec data files """

        journals_set = set()
        for record in rows:
            journal_code = record.get("JournalCode")
            journal_code = self._shorten_code('journal_code', journal_code, 5, cache)
            journal_name = record.get("JournalLib")

            # Check for an existing journal
            if journal_code and journal_code not in journals_set:
                journals_set.add(journal_code)

                # Journals are created with the "general" type
                # The type will be inferred by _get_journal_type() once that the move_lines have been imported
                existing = cache["account.journal"].get(journal_code, None)
                if not existing:
                    data = {
                        "company_id": self.company_id.id,
                        "code": journal_code,
                        "name": "FEC-%s" % (journal_name or journal_code),
                        "type": "general"
                    }
                    yield data

    def _generator_fec_res_partner(self, rows, cache):
        """ Import the partners from FEC data files """

        partners_set = set()
        for record in rows:
            partner_ref = record.get("CompAuxNum", "")
            partner_name = record.get("CompAuxLib", "")
            account_code = record.get("CompteNum", "")

            # Check for an existing partner with the same name or ref
            partner_key = partner_ref or partner_name
            if partner_key and partner_key not in partners_set:
                partners_set.add(partner_key)

                # Check if the partner is already existing
                existing = cache["res.partner.ref"].get(partner_ref, None)
                if not existing:
                    data = {
                        "company_id": self.company_id.id,
                        "name": partner_name,
                        "ref": partner_ref,
                    }

                    # Setup account properties
                    account = account_code and cache["account.account"].get(account_code.rstrip('0'), None)
                    if account:
                        if account.account_type == 'asset_receivable':
                            data["property_account_receivable_id"] = account.id
                        elif account.account_type == 'liability_payable':
                            data["property_account_payable_id"] = account.id

                    yield data

    def _check_rounding_issues(self, moves_dict, balance_dict):
        """ If journals are unbalanced, check if they can be balanced by adding some counterpart line
            when a rounding issue is found. """

        # Get the accounts for the debit and credit differences
        debit_account, credit_account = [
            self.env["account.account"].search(
                [('code', '=like', code), ('company_id', '=', self.company_id.id)],
                order='code',
                limit=1,
            ) for code in ('6580%', '7580%')
        ]

        # Check the moves for rounding issues
        currency = self.company_id.currency_id
        imbalanced_journals = set()
        for key, move in moves_dict.items():
            balance_data = balance_dict[key]
            balance, matching = balance_data["balance"], balance_data["matching"]

            # If it's tolerable, create a counterpart line
            if 0 < abs(balance) <= currency.rounding:
                data = {
                    "name": _("Import rounding difference"),
                    "account_id": debit_account.id if balance > 0 else credit_account.id,
                    "credit": 0.0 if balance > 0 else abs(balance),
                    "debit": balance if balance > 0 else 0.0,
                    "fec_matching_number": matching or False,
                }
                move["line_ids"].append(fields.Command.create(data))

            # If it's not tolerable, mark the journal and the date as incorrect
            elif abs(balance) > currency.rounding:
                imbalanced_journals.add(move["journal_id"])

        return imbalanced_journals

    def _try_balance_moves(self, key_function, currency, journal_id, journal_code, lines_grouped_by_date):
        """ Try to balance moves by grouping them by date.
            The key_function is applied on the date to group lines toghether.
            Throws UnbalancedMovesError if the grouping cannot produce zero-balanced moves,
            Returns {date_key: list_of_lines}"""

        # Compute the balance, using the key_function to group the lines
        groups = {}
        for lines_date, lines in lines_grouped_by_date.items():
            key = key_function(lines_date)
            balance = sum([line["debit"] - line["credit"] for line in lines])
            old_balance, old_lines = groups.get(key, (0.0, []))
            groups[key] = [old_balance + balance, old_lines + lines]

        # Check if there's any group with a non-zero balance
        rebalanced_moves = {}
        for key, (balance, lines) in groups.items():

            # If there is a non-zero balance, rebalanced_moves is invalid
            # Exit the function indicating failure.
            if not float_is_zero(balance, precision_rounding=currency.rounding):
                raise UnbalancedMovesError("Cannot group moves")

            # Build the keys with the same format that was used in moves_dict
            if len(key) == 2:
                move_key = "%s/%04d%02d" % (journal_code, *key)
                move_date = datetime.date(*key, 1)
            else:
                move_key = "%s/%04d%02d%02d" % (journal_code, *key)
                move_date = datetime.date(*key)

            # Store the valid move in a dictionary
            rebalanced_moves[move_key] = {
                "company_id": self.company_id.id,
                "name": move_key,
                "date": move_date,
                "journal_id": journal_id,
                "line_ids": [fields.Command.create(line) for line in lines]
            }

        return rebalanced_moves

    def _check_imbalanced_journals(self, cache, moves_dict, balance_dict, imbalanced_journals, imbalances):
        """ If there are still imbalanced moves, try to re-group the lines by journal/date
            for the imbalanced journals, to see if now they balance altogether. """
        currency = self.company_id.currency_id

        # If there still are imbalanced journals, clear the moves_dict
        # of all the moves with the imbalanced journal_id and put them aside
        imbalanced_moves = []
        for move_key in list(moves_dict.keys()):
            if moves_dict[move_key]["journal_id"] in imbalanced_journals:
                imbalanced_moves.append(moves_dict.pop(move_key))

        # For each journal, try to group it with different key functions
        # until you find one that produces groups of lines which are zero-balanced
        journal_codes = {journal.id: journal_code for journal_code, journal in cache["account.journal"].items()}
        for journal_id in imbalanced_journals:
            journal_code = journal_codes[journal_id]
            lines_grouped_by_date = imbalances[journal_id]
            for _grouping, key_function in [
                ("day", lambda x: (x.year, x.month, x.day)),
                ("month", lambda x: (x.year, x.month))
            ]:
                # If a grouping that makes all moves have a zero-balance has been found,
                # we save the moves and stop looking for other ways of grouping them.
                try:
                    rebalanced_moves = self._try_balance_moves(
                        key_function, currency, journal_id, journal_code, lines_grouped_by_date)
                    moves_dict.update(rebalanced_moves)
                    break
                except UnbalancedMovesError:
                    pass

            # If no grouping (day/month) can make all moves be balanced,
            # then alert the user that the file cannot be understood
            else:
                balance_issues = ""
                for move in imbalanced_moves:
                    balance_key = "%s/%s" % (journal_code, move["name"])
                    balance = balance_dict[balance_key]["balance"]
                    if not float_is_zero(balance, precision_rounding=currency.rounding):
                        balance_issues += _("Move with name '%s' has a balance of %s\n",
                                            move["name"], float_repr(balance, currency.decimal_places))
                raise UserError(_("Moves report incorrect balances:\n%s", balance_issues))

    def _normalize_float_value(self, record, key):
        """ Normalize a float string value inside a dictionary """
        return float((record.get(key, "") or "0.0").replace(",", "."))

    def _get_credit_debit_balance(self, record, currency):
        """ The credit/debit may be specified as Montant/Sens
            Sens must be in ['C', 'D'] which mean Credit/Debit) """

        if "Montant" in record and "Sens" in record:
            sens = record.get("Sens", "").upper()
            montant = self._normalize_float_value(record, "Montant")
            credit = montant if sens == "C" else 0.0
            debit = montant if sens == "D" else 0.0
        else:
            credit = self._normalize_float_value(record, "Credit")
            debit = self._normalize_float_value(record, "Debit")

        credit = currency.round(credit)
        debit = currency.round(debit)

        # Negative values must be inverted
        if credit < 0 or debit < 0:
            debit, credit = -credit, -debit

        balance = currency.round(debit - credit)

        return credit, debit, balance

    def _generator_fec_account_move(self, rows, cache):
        """ Import the moves from the FEC files.
            The first loop collects informations, then in a second loop, move_line level information is assembled and the data can be yielded.

            If partner information is found on a line, it has to be brought from move_line level to move level.

            The credit/debit may be specified as Montant/Sens.
            Sens must be in ['C', 'D'] which mean Credit/Debit).
        """

        moves_dict = {}

        # Keeps track of moves grouped by journal_id and move_date, it helps with imbalances
        imbalances = defaultdict(lambda: defaultdict(list))

        # Keeps the move's balance after summing each line's debit and credit
        balance_dict = {}

        for idx, record in enumerate(rows):

            # Move data -----------------------------------------

            # The move_name sometimes may be not provided, use the piece_ref instead
            piece_ref = record.get("PieceRef", "")
            ecriture_num = record.get("EcritureNum", "")
            move_name = ecriture_num or piece_ref
            if not move_name:
                raise UserError(_("Line %s has an invalid move name", idx))

            # The move_date sometimes is not provided, use the piece_date instead
            piece_date = record.get("PieceDate", "")
            piece_date = piece_date and datetime.datetime.strptime(piece_date, "%Y%m%d")
            move_date = record.get("EcritureDate", "")
            move_date = (move_date and datetime.datetime.strptime(move_date, "%Y%m%d")) or piece_date
            partner_ref = record.get("CompAuxNum", "")
            partner_name = record.get("CompAuxLib", "")
            journal_code = record.get("JournalCode", "")

            # Move line data ------------------------------------
            move_line_name = record.get("EcritureLib", "")
            account_code = record.get("CompteNum", "")
            currency_name = record.get("Idevise", "")
            amount_currency = self._normalize_float_value(record, "Montantdevise")
            matching = record.get("EcritureLet", "")

            # Move import --------------------------------------

            # Journal
            journal = cache["account.journal"].get(journal_code, None)
            if not journal:

                # Look for a shortened code
                journal_code = cache.get("mapping_journal_code", {}).get(journal_code, None)
                if journal_code:
                    journal = cache["account.journal"].get(journal_code, None)

                if not journal:
                    raise UserError(_("Line %s has an invalid journal code", idx))

            # Use the journal and the move_name as key for the move in the moves_dict
            move_key = "%s/%s" % (journal.code, move_name)

            # Many move_lines may belong to the same move, the move info gets saved in the moves_dict
            data = moves_dict.get(move_key, {
                "company_id": self.company_id.id,
                "name": move_name,
                "date": move_date,
                "ref": piece_ref,
                "journal_id": journal.id,
                "line_ids": [],
            })
            balance_data = balance_dict.get(move_key, {"balance": 0.0, "matching": False})

            # Move line import ----------------------------------

            # Account
            account = cache["account.account"].get(account_code.rstrip('0'), None)
            if not account:
                raise UserError(_("Line %s has an invalid account %r", idx, account_code))

            # Build the basic data
            line_data = {
                "company_id": self.company_id.id,
                "name": move_line_name,
                "ref": piece_ref,
                "account_id": account.id,
                "fec_matching_number": matching or False,
                "tax_ids": [],  # Avoid default taxes on the accounts to be set
            }

            # Save the matching number for eventual balance issues
            balance_data["matching"] = balance_data["matching"] or matching or False

            # Partner. As we are creating Journal Entries and not invoices/vendor bills,
            # the partner information will stay just on the line.
            # It may be updated in the post-processing after all the imports are done.
            partner = cache["res.partner.ref"].get(partner_ref, None)
            if not partner:
                partner = cache["res.partner"].get(partner_name, None)
            line_data["partner_id"] = partner.id if partner else False

            # Currency
            if currency_name in cache["res.currency"] and amount_currency:
                currency = cache["res.currency"][currency_name]
                line_data.update({
                    "currency_id": currency.id,
                    "amount_currency": amount_currency,
                })
            else:
                currency = self.company_id.currency_id

            # Round the values, save the total balance to detect issues
            credit, debit, balance = self._get_credit_debit_balance(record, currency)
            line_data["credit"] = credit
            line_data["debit"] = debit
            balance_data["balance"] = currency.round(balance_data["balance"] + balance)

            # Montantdevise can be positive while the line is credited:
            # => amount_currency and balance (debit - credit) should always have the same sign
            if currency_name in cache["res.currency"] and amount_currency and line_data['amount_currency'] * balance < 0:
                line_data["amount_currency"] *= -1

            # Append the move_line data to the move
            data["line_ids"].append(fields.Command.create(line_data))

            # Update the data in the moves_dict
            moves_dict[move_key] = data
            imbalances[journal.id][move_date].append(line_data)
            balance_dict[move_key] = balance_data

        # Check for imbalanced journals, fix rounding issues
        imbalanced_journals = self._check_rounding_issues(moves_dict, balance_dict)

        # If there are still imbalanced, journals, try to re-group the lines by journal/date,
        # to see if now they balance altogether
        if imbalanced_journals:
            self._check_imbalanced_journals(cache, moves_dict, balance_dict, imbalanced_journals, imbalances)

        yield from moves_dict.values()

    # -----------------------------------
    # Templates
    # -----------------------------------

    def _gather_templates(self):
        """ Find all the templates for the considered entities.
            These templates will be used to fill out missing information coming from the records.
            For accounts, account_type and reconcile flags are used.  """

        # account.account templates
        domain = [('chart_template_id', '=', self.env.company.chart_template_id.id)]
        account_templates = self.env["account.account.template"].search_read(domain, ['code', 'display_name', 'account_type', 'reconcile'])

        all_templates = {"account.account": {x['code']: x for x in account_templates}}
        return all_templates

    def _apply_template(self, templates, model, record):
        """ Given a template, apply its fields to the record, overwriting existing ones.
            As for accounts, it matches the significant digits (right zeros are stripped) of the code with those of the template's code.
        """
        if model == "account.account":
            for limit in [999, 3, 2]:
                def normalize_code(x):
                    return x[:limit].rstrip('0')
                template = next((v for k, v in templates.items() if normalize_code(k) == normalize_code(record['code'])), {})
                if template:
                    for key, value in template.items():
                        if key not in ['id', 'code']:
                            record[key] = value
                    break

    # ------------------------------------
    # Utility functions
    # ------------------------------------

    def _get_journal_type(self, journals, ratio, min_moves):
        """
            Determine the type of journal given the current situation with moves and accounts.

            'bank'      Moves in these journals will always have a line (debit or credit) impacting a liquidity account.
                        ('cash' / 'bank' can be interchanged, so 'bank' is set everywhere when this condition is met)
            'sale'      Moves in these journals will mostly have debit lines on receivable accounts and credit lines on tax income accounts.
                        Sale refund journal items will be debit/credit inverted.
            'purchase'  Moves in these journals will mostly have credit lines on payable accounts and debit lines on expense accounts.
                        Purchase refund journal items will be debit/credit inverted.
            'general'   For everything else.

            A minimum of 3 moves is necessary for journal_type identification.
            A threshold of 70% of moves must correspond to a criteria for a journal_type to be determined.

            Example:
                Journal id=5
                Moves: total=4
                    has a sale account line and no purchase account line=0     ratio=0
                    has a purchase account line and no sale account line=1     ratio=0.25
                    has a liquidity account line                        =3     ratio=0.75

            The journal type is "bank", because the bank moves ratio 3/4 (0.75) exceeds the threshold (0.7)

        """

        # Ensure data consistency
        self.env.flush_all()

        # Query the database to determine the journal type
        # The sum_move_lines_per_move query determines the type of the account of the lines
        # The sum_moves_per_journal query counts the account types on the lines for each move
        # The main query compares the sums with the threshold and determines the type
        if self.pool['account.journal'].name.translate:
            lang = self.env.user.lang or get_lang(self.env).code
            aj_name = f"COALESCE(aj.name->>'{lang}', aj.name->>'en_US')"
        else:
            aj_name = 'aj.name'
        sql = f"""
            WITH sum_move_lines_per_move as (
                SELECT aml.journal_id as journal_id,
                       {aj_name} as journal_name,
                       aml.move_id,
                       SUM(CASE WHEN aa.account_type IN ('asset_cash','liability_credit_card') THEN 1 ELSE 0 END) as bank,
                       SUM(CASE aa.account_type WHEN 'asset_receivable' THEN 1 ELSE 0 END) as sale,
                       SUM(CASE aa.account_type WHEN 'liability_payable' THEN 1 ELSE 0 END) as purchase
                  FROM account_move_line aml
                       JOIN account_account aa on aa.id = aml.account_id
                       JOIN account_journal aj on aj.id = aml.journal_id
                 WHERE aj.id in %s
              GROUP BY journal_id, journal_name, move_id),

            sum_moves_per_journal as (
                SELECT journal_id,
                       journal_name,
                       SUM(CASE WHEN bank > 0 THEN 1 ELSE 0 END) as bank_sum,
                       SUM(CASE WHEN sale > 0 THEN 1 ELSE 0 END) as sale_sum,
                       SUM(CASE WHEN purchase > 0 THEN 1 ELSE 0 END) as purchase_sum,
                       COUNT(*) as moves,
                       CAST(COUNT(*) * %s as integer) as threshold
                  FROM sum_move_lines_per_move
              GROUP BY journal_id, journal_name)

            SELECT journal_id,
                   CASE WHEN moves < %s THEN 'general'
                        WHEN bank_sum >= threshold THEN 'bank'
                        WHEN sale_sum >= threshold and purchase_sum = 0 THEN 'sale'
                        WHEN purchase_sum >= threshold and sale_sum = 0 THEN 'purchase'
                        ELSE 'general'
                    END as journal_type
              FROM sum_moves_per_journal
          ORDER BY journal_id
        """

        # Yield the records
        self.env.cr.execute(sql, (tuple(journals.ids), ratio, min_moves))
        yield from self.env.cr.fetchall()

    def _setup_bank_journal(self, journal):
        """ The bank journal needs a default liquidity account and outstanding payments accounts to be set """

        # Determine the most used liquidity account on the journal and set it as default on the Bank Journal
        sql = """
            SELECT aa.id,
                   COUNT(*) as frequency
              FROM account_move_line aml
                   JOIN account_account aa on aa.id = aml.account_id
                   JOIN account_journal aj on aj.id = aml.journal_id
              WHERE aj.id = %s
                    and (aa.account_type = 'asset_cash' OR aa.account_type = 'liability_credit_card')
           GROUP BY aa.id
           ORDER BY frequency DESC
        """
        self.env.cr.execute(sql, (journal.id, ))
        record = self.env.cr.fetchone()
        if record:
            journal.default_account_id = record[0]

        # Set default suspense account on the Bank Journal
        journal.suspense_account_id = self.company_id.account_journal_suspense_account_id

    def _reconcile_imported_move_lines(self, moves):
        """ Reconcile imported move lines, the matching is done between the fields ['account_id', 'matching_number'] """

        # Ensure that the database is aligned
        self.env.flush_all()

        # Retrieve the move lines
        sql = """ SELECT ARRAY_AGG(id) ids,
                         account_id
                    FROM account_move_line
                   WHERE account_id IS NOT NULL
                         AND fec_matching_number IS NOT NULL
                         AND reconciled = False
                         AND move_id in %s
                GROUP BY account_id, fec_matching_number
                  HAVING COUNT(*) > 1 """

        # Set the account as reconcilable and actively reconcile the lines
        self.env.cr.execute(sql, (tuple(moves.ids), ))
        for record in self.env.cr.fetchall():
            matched_move_line_ids, account_id = record
            self.env["account.account"].browse(account_id).reconcile = True
            lines = self.env["account.move.line"].browse(matched_move_line_ids)

            # Since the accounts are now 'reconcile', we need to force the update of the residual amounts.
            self.env.add_to_compute(lines._fields['amount_residual'], lines)

            lines.with_context(no_exchange_difference=True, no_cash_basis=True).reconcile()

    def _post_process(self, journals, moves):
        """ Post-process the imported entities.
            Sets the partner_id on account.moves if they target Purchase/Sale Journals"""
        for move in moves:
            for move_line in move.line_ids:
                if move_line.partner_id:
                    move.partner_id = move_line.partner_id
                    break

    def _build_import_cache(self):
        """ Build a cache with all the data needed by the generators, so that the query is done just one time """

        # Retrieve all the data from the database
        accounts = self.env["account.account"].search([("company_id", "=", self.company_id.id)])
        journals = self.env["account.journal"].search([("company_id", "=", self.company_id.id)])
        partners = self.env["res.partner"].search([("company_id", "in", [self.company_id.id, False])])
        currencies = self.env["res.currency"].search([])

        # Build the cache dictionary
        return {
            "account.account": {x["code"].rstrip('0'): x for x in accounts},
            "account.journal": {x["code"]: x for x in journals},
            "res.currency": {x["name"]: x for x in currencies},
            "res.partner": {x["name"]: x for x in partners},
            "res.partner.ref": {x["ref"]: x for x in partners},
        }

    def _update_import_cache(self, cache, model, records):
        """ Update the cache with all the records created the generators.
            Each model can have a different key, and the update handles it."""

        get_key = {
            "account.account": lambda x: x.code.rstrip('0'),
            "account.journal": lambda x: x.code,
            "res.currency": lambda x: x.name,
            "res.partner": lambda x: x.name
        }.get(model, None)
        if get_key:
            cache[model].update({get_key(x): x for x in records})

        if model == "res.partner":
            cache["res.partner.ref"].update({x["ref"]: x for x in records})

    # -----------------------------------
    # Main methods
    # -----------------------------------

    def action_import(self):
        """ Action called by the Import button """
        return self._import_files()

    def _import_files(self, models=None):
        """ Start the import by gathering generators and templates and applying them to attached files. """

        # Basic checks to start
        if not self.company_id.account_fiscal_country_id or not self.company_id.chart_template_id:
            action = self.env.ref('account.action_account_config')
            raise RedirectWarning(_('You should install a Fiscal Localization first.'), action.id, _('Accounting Settings'))

        # Models list can be injected for testing purposes
        if not models:
            models = ["account.account", "account.journal", "res.partner", "account.move"]

        # In Odoo, move names follow sequences based on the year, so the checks complain
        # if the year present in the move's name doesn't match with the move's date.
        # This is unimportant here since we are importing existing moves from external data.
        # The workaround is to set the sequence.mixin.constraint_start_date parameter
        # to the date of the oldest move (defaulting to today if there is no move at all).
        if "account.move" in models:
            domain = [("company_id", "=", self.company_id.id)]
            start_date = self.env["account.move"].search(domain, limit=1, order="date asc").date or fields.Date.today()
            start_date_str = start_date.strftime("%Y-%m-%d")
            self.env["ir.config_parameter"].sudo().set_param("sequence.mixin.constraint_start_date", start_date_str)

        # Build a cache with all the cache needed by the generators, so that the query is done just one time
        cache = self._build_import_cache()

        all_records = {}
        all_templates = self._gather_templates()
        rows = self._get_rows(self.attachment_id, self.attachment_name)

        # For each file provided, cycle over each model
        for model in models:

            _logger.info("%s FEC import started", model)

            # Retrieve the templates
            model_templates = all_templates.get(model, {})

            # Generate the records for the model
            records = []
            generator_name = "_generator_fec_%s" % model.replace(".", "_")
            generator = getattr(self, generator_name)

            # Loop over generated records and apply a template if a matching one is found
            for idx, record in enumerate(generator(rows, cache)):
                self._apply_template(model_templates, model, record)
                records.append({"values": record})

                # Notify the user every 100 records
                if idx and idx % 100 == 0:
                    _logger.info("%5d records gathered", idx)

            # Import records, then flush and update the cache with the inserted records
            if records:
                all_records[model] = self.env[model]._load_records(records)
                self._update_import_cache(cache, model, all_records[model])

        # If there are moves, post them
        moves = all_records.get("account.move", [])
        if moves:
            _logger.info("Posting moves...")
            moves.action_post()

            _logger.info("Reconciling move_lines...")
            self._reconcile_imported_move_lines(moves)

            journals = all_records.get("account.journal", [])
            if journals:
                journals_dict = {journal.id: journal for journal in cache["account.journal"].values()}
                for journal_id, journal_type in self._get_journal_type(journals, ratio=0.7, min_moves=3):
                    # Changing the journal type will compute a mail alias for the journal if it's sale/purchase, using its name.
                    # If multiple distinct journals in the file share the same name, they will have the same alias and it will crash.
                    # We set the context to bypass that behavior. In the context of FEC import, creating the alias by default is not what we want.
                    journal = journals_dict[journal_id].with_context(account_journal_skip_alias_sync=True)
                    journal.type = journal_type

                    # The bank journal needs a default liquidity account and outstanding payments accounts to be set
                    if journal_type == 'bank':
                        self._setup_bank_journal(journal)

                self._post_process(journals, moves)

        return {
            "type": "ir.actions.client",
            "tag": "reload",
        }
