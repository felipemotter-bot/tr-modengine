/** @odoo-module */

import {Component, useState} from "@odoo/owl";
import {registry} from "@web/core/registry";
import {usePopover} from "@web/core/popover/popover_hook";
import {standardFieldProps} from "@web/views/fields/standard_field_props";
import {_lt} from "@web/core/l10n/translation";
import {formatFloat} from "@web/views/fields/formatters";
import {parseFloat as odooParseFloat} from "@web/views/fields/parsers";

/**
 * Compute cash discount needed to reach a desired untaxed total.
 *
 * @param {Number} currentFinancialTotal - current amount_financial_total (with cash+fob applied)
 * @param {Number} cashDiscount - current cash discount %
 * @param {Number} fobDiscount - current fob discount %
 * @param {Number} desiredTotal - target financial total
 * @returns {Object} {cashDiscount, resultingTotal, error}
 */
function computeOrderDiscount(
  currentFinancialTotal,
  cashDiscount,
  fobDiscount,
  desiredTotal
) {
  // Reverse the current discount to get the base (before cash+fob)
  const currentTotalDiscount = (cashDiscount + fobDiscount) / 100;
  const baseTotal =
    currentTotalDiscount < 1 ? currentFinancialTotal / (1 - currentTotalDiscount) : 0;

  if (baseTotal <= 0) {
    return {cashDiscount: 0, resultingTotal: 0, error: _lt("No order lines.")};
  }
  if (desiredTotal <= 0) {
    return {
      cashDiscount: 0,
      resultingTotal: 0,
      error: _lt("Amount must be greater than zero."),
    };
  }
  if (desiredTotal >= baseTotal) {
    return {
      cashDiscount: 0,
      resultingTotal: baseTotal,
      error: _lt("Amount must be lower than the current financial total."),
    };
  }

  const totalDiscountNeeded =
    Math.round((1 - desiredTotal / baseTotal) * 1000000) / 10000;
  const newCashDiscount =
    Math.round((totalDiscountNeeded - fobDiscount) * 10000) / 10000;

  if (newCashDiscount < 0) {
    return {
      cashDiscount: 0,
      resultingTotal: baseTotal * (1 - fobDiscount / 100),
      error: _lt("FOB discount alone already exceeds this amount."),
    };
  }
  if (newCashDiscount > 99) {
    return {
      cashDiscount: 0,
      resultingTotal: 0,
      error: _lt("Cash discount cannot exceed 99%."),
    };
  }

  const resultingTotal =
    Math.round(baseTotal * (1 - (newCashDiscount + fobDiscount) / 100) * 100) / 100;

  return {cashDiscount: newCashDiscount, resultingTotal, error: ""};
}

class OrderDiscountPopoverContent extends Component {
  setup() {
    this.state = useState({
      desiredAmount: "",
    });
  }

  get preview() {
    let desired = 0;
    try {
      desired = odooParseFloat(String(this.state.desiredAmount));
    } catch {
      return null;
    }
    if (!desired || isNaN(desired)) {
      return null;
    }
    return computeOrderDiscount(
      this.props.currentFinancialTotal,
      this.props.cashDiscount,
      this.props.fobDiscount,
      desired
    );
  }

  onAmountInput(ev) {
    let val = ev.target.value.replace(/\./g, ",");
    const parts = val.split(",");
    if (parts.length > 2) {
      val = parts[0] + "," + parts.slice(1).join("");
    }
    ev.target.value = val;
    this.state.desiredAmount = val;
  }

  formatDiscount(value) {
    return formatFloat(value, {digits: [false, 4]});
  }

  formatPrice(value) {
    return formatFloat(value, {digits: [false, 2]});
  }

  onAmountFocus(ev) {
    if (parseFloat(ev.target.value) === 0) {
      ev.target.select();
    }
  }

  onApply() {
    const result = this.preview;
    if (!result || result.error) {
      return;
    }
    this.props.onApply(result.cashDiscount);
    this.props.close();
  }
}
OrderDiscountPopoverContent.template =
  "tr_commercial_policy.OrderDiscountPopoverContent";

class OrderDiscountCalculator extends Component {
  setup() {
    this.popover = usePopover();
    this.closePopover = null;
  }

  get formattedValue() {
    const val = this.props.value || 0;
    return formatFloat(val, {digits: [false, 4]});
  }

  get isVisible() {
    const state = this.props.record.data.state;
    return ["draft", "sent"].includes(state) && !this.props.readonly;
  }

  async onInputChange(ev) {
    let val = 0;
    try {
      val = odooParseFloat(ev.target.value);
    } catch {
      val = 0;
    }
    if (this.props.update) {
      await this.props.update(val);
    }
  }

  onButtonClick(ev) {
    if (this.closePopover) {
      this.closePopover();
      this.closePopover = null;
      return;
    }
    this.closePopover = this.popover.add(
      ev.currentTarget,
      OrderDiscountPopoverContent,
      {
        currentFinancialTotal: this.props.record.data.amount_financial_total || 0,
        cashDiscount: this.props.record.data.cash_discount || 0,
        fobDiscount: this.props.record.data.fob_discount || 0,
        onApply: this.onApply.bind(this),
      },
      {
        position: "bottom",
        closeOnClickAway: true,
        onClose: () => {
          this.closePopover = null;
        },
      }
    );
  }

  async onApply(newCashDiscount) {
    await this.props.record.update({cash_discount: newCashDiscount});
  }
}
OrderDiscountCalculator.template = "tr_commercial_policy.OrderDiscountCalculator";
OrderDiscountCalculator.props = {...standardFieldProps};
OrderDiscountCalculator.supportedTypes = ["float"];

registry.category("fields").add("order_discount_calculator", OrderDiscountCalculator);
