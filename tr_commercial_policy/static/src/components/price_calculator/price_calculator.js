/** @odoo-module */

import {Component, useState} from "@odoo/owl";
import {registry} from "@web/core/registry";
import {usePopover} from "@web/core/popover/popover_hook";
import {standardFieldProps} from "@web/views/fields/standard_field_props";
import {_lt} from "@web/core/l10n/translation";
import {formatFloat} from "@web/views/fields/formatters";
import {parseFloat as odooParseFloat} from "@web/views/fields/parsers";

/**
 * Pure helper: compute discount decomposition from a desired price.
 *
 * @param {Number} referencePrice - line reference price
 * @param {Number} desiredPrice - target price entered by user
 * @param {Number} sellerDiscountMax - max seller discount from profile
 * @returns {Object} {sellerDiscount, extraDiscount, resultingPrice, error}
 */
function computeDiscountDecomposition(referencePrice, desiredPrice, sellerDiscountMax) {
  if (referencePrice <= 0) {
    return {sellerDiscount: 0, extraDiscount: 0, resultingPrice: 0, error: ""};
  }
  if (desiredPrice <= 0) {
    return {
      sellerDiscount: 0,
      extraDiscount: 0,
      resultingPrice: 0,
      error: _lt("Price must be greater than zero."),
    };
  }
  if (desiredPrice > referencePrice) {
    return {
      sellerDiscount: 0,
      extraDiscount: 0,
      resultingPrice: referencePrice,
      error: _lt("Price must be lower than the reference price."),
    };
  }

  const totalNeeded = Math.round((1 - desiredPrice / referencePrice) * 1000000) / 10000;

  if (totalNeeded > 99) {
    return {
      sellerDiscount: 0,
      extraDiscount: 0,
      resultingPrice: 0,
      error: _lt("Total discount cannot exceed 99%."),
    };
  }

  let sellerDiscount = 0;
  let extraDiscount = 0;
  if (totalNeeded <= sellerDiscountMax) {
    sellerDiscount = Math.round(totalNeeded * 10000) / 10000;
  } else {
    sellerDiscount = Math.round(sellerDiscountMax * 10000) / 10000;
    extraDiscount = Math.round((totalNeeded - sellerDiscountMax) * 10000) / 10000;
  }

  const resultingPrice =
    Math.round(referencePrice * (1 - (sellerDiscount + extraDiscount) / 100) * 100) /
    100;

  return {sellerDiscount, extraDiscount, resultingPrice, error: ""};
}

class PriceCalculatorPopoverContent extends Component {
  setup() {
    this.state = useState({
      desiredPrice: "",
      reason: "",
    });
  }

  get preview() {
    let desired = 0;
    try {
      desired = odooParseFloat(String(this.state.desiredPrice));
    } catch {
      return null;
    }
    if (!desired || isNaN(desired)) {
      return null;
    }
    return computeDiscountDecomposition(
      this.props.referencePrice,
      desired,
      this.props.sellerDiscountMax
    );
  }

  onPriceInput(ev) {
    this.state.desiredPrice = ev.target.value;
  }

  formatDiscount(value) {
    return formatFloat(value, {digits: [false, 4]});
  }

  formatPrice(value) {
    return formatFloat(value, {digits: [false, 2]});
  }

  onPriceFocus(ev) {
    if (parseFloat(ev.target.value) === 0) {
      ev.target.select();
    }
  }

  onApply() {
    const result = this.preview;
    if (!result || result.error) {
      return;
    }
    this.props.onApply(result.sellerDiscount, result.extraDiscount, this.state.reason);
    this.props.close();
  }
}
PriceCalculatorPopoverContent.template =
  "tr_commercial_policy.PriceCalculatorPopoverContent";

class PriceCalculatorButton extends Component {
  setup() {
    this.popover = usePopover();
    this.closePopover = null;
  }

  get isVisible() {
    const state = this.props.record.model.root.data.state;
    const referencePrice = this.props.record.data.reference_price || 0;
    return ["draft", "sent"].includes(state) && referencePrice > 0;
  }

  onButtonClick(ev) {
    if (this.closePopover) {
      this.closePopover();
      this.closePopover = null;
      return;
    }
    this.closePopover = this.popover.add(
      ev.currentTarget,
      PriceCalculatorPopoverContent,
      {
        referencePrice: this.props.record.data.reference_price || 0,
        sellerDiscountMax: this.props.record.data.seller_discount_max || 0,
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

  async onApply(sellerDiscount, extraDiscount, reason) {
    if (this.props.setDirty) {
      this.props.setDirty(true);
    }
    const changes = {
      seller_discount: sellerDiscount,
      extra_discount: extraDiscount,
      extra_discount_reason: extraDiscount > 0 ? reason || "" : "",
    };
    await this.props.record.update(changes);
  }
}
PriceCalculatorButton.template = "tr_commercial_policy.PriceCalculatorButton";
PriceCalculatorButton.props = {...standardFieldProps};
PriceCalculatorButton.supportedTypes = ["float"];

registry.category("fields").add("price_calculator_button", PriceCalculatorButton);
