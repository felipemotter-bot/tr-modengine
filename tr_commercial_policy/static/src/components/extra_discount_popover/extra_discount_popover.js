/** @odoo-module */

import {Component, useState} from "@odoo/owl";
import {registry} from "@web/core/registry";
import {usePopover} from "@web/core/popover/popover_hook";
import {standardFieldProps} from "@web/views/fields/standard_field_props";
import {_lt} from "@web/core/l10n/translation";
import {formatFloat} from "@web/views/fields/formatters";
import {parseFloat as odooParseFloat} from "@web/views/fields/parsers";

class ExtraDiscountPopoverContent extends Component {
  setup() {
    const initial = this.props.extraDiscount || 0;
    this.state = useState({
      extraDiscountText: initial ? formatFloat(initial, {digits: [false, 4]}) : "",
      reason: this.props.reason || "",
      error: "",
    });
  }

  onDiscountFocus(ev) {
    if (!this.state.extraDiscountText || this.state.extraDiscountText === "0") {
      ev.target.select();
    }
  }

  onDiscountInput(ev) {
    let val = ev.target.value.replace(/\./g, ",");
    const parts = val.split(",");
    if (parts.length > 2) {
      val = parts[0] + "," + parts.slice(1).join("");
    }
    ev.target.value = val;
    this.state.extraDiscountText = val;
  }

  _parseDiscount() {
    try {
      return odooParseFloat(this.state.extraDiscountText) || 0;
    } catch {
      return 0;
    }
  }

  onApply() {
    const discount = this._parseDiscount();
    this.state.error = "";
    if (discount < 0) {
      this.state.error = _lt("Extra discount cannot be negative.");
      return;
    }
    if (discount > 99) {
      this.state.error = _lt("Extra discount cannot exceed 99%.");
      return;
    }
    const sellerDiscount = this.props.sellerDiscount || 0;
    const total = sellerDiscount + discount;
    if (total > 99) {
      this.state.error = _lt("Total discount (seller + extra) cannot exceed 99%.");
      return;
    }
    this.props.onApply(discount, this.state.reason);
    this.props.close();
  }

  onClear() {
    this.props.onApply(0, "");
    this.props.close();
  }
}
ExtraDiscountPopoverContent.template =
  "tr_commercial_policy.ExtraDiscountPopoverContent";

class ExtraDiscountField extends Component {
  setup() {
    this.popover = usePopover();
    this.closePopover = null;
  }

  get extraDiscount() {
    return this.props.record.data.extra_discount || 0;
  }

  formatDiscount(value) {
    return formatFloat(value, {digits: [false, 4]});
  }

  get hasExtraDiscount() {
    return this.extraDiscount > 0;
  }

  get isEditable() {
    const parentState = this.props.record.data["parent.state"];
    const state = this.props.record.model.root.data.state;
    return (parentState || state) === "draft";
  }

  onButtonClick(ev) {
    if (this.closePopover) {
      this.closePopover();
      this.closePopover = null;
      return;
    }
    this.closePopover = this.popover.add(
      ev.currentTarget,
      ExtraDiscountPopoverContent,
      {
        extraDiscount: this.props.record.data.extra_discount || 0,
        reason: this.props.record.data.extra_discount_reason || "",
        sellerDiscount: this.props.record.data.seller_discount || 0,
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

  async onApply(discount, reason) {
    // Use props.update for the main field (marks it dirty for the form save)
    if (this.props.update) {
      await this.props.update(discount);
    }
    // Update reason via record.update (secondary field)
    await this.props.record.update({
      extra_discount_reason: reason || "",
    });
  }
}
ExtraDiscountField.template = "tr_commercial_policy.ExtraDiscountField";
ExtraDiscountField.props = {...standardFieldProps};
ExtraDiscountField.supportedTypes = ["float"];

registry.category("fields").add("extra_discount_popover", ExtraDiscountField);
