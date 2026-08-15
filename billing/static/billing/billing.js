document.addEventListener("DOMContentLoaded", function () {
    var container = document.getElementById("product-rows");
    var template = document.getElementById("product-row-template");
    var addButton = document.getElementById("add-row-button");
    var productDataEl = document.getElementById("products-data");
    var catalogTable = document.getElementById("product-catalog-table");
    var filterInput = document.getElementById("product-filter");

    if (!container) {
        return;
    }

    // product_id -> {name, price, taxPercent, stock}, built from the
    // server-rendered product list. Used only for an instant, informational
    // preview in the browser -- the server recalculates everything
    // authoritatively (and re-validates stock/product existence) on submit.
    var products = {};
    if (productDataEl) {
        JSON.parse(productDataEl.textContent).forEach(function (p) {
            products[p.id] = { name: p.name, price: parseFloat(p.price), taxPercent: parseFloat(p.tax_percent), stock: p.stock };
        });
    }

    function round2(value) {
        return Math.round((value + Number.EPSILON) * 100) / 100;
    }

    function formatMoney(value, decimals) {
        return "Rs. " + value.toFixed(decimals === undefined ? 2 : decimals);
    }

    /* ---------- Line items + order summary ---------- */

    function updateRow(row) {
        var productInput = row.querySelector("[data-product-input]");
        var quantityInput = row.querySelector("[data-quantity-input]");
        var lineTotalEl = row.querySelector("[data-line-total]");
        var hintEl = row.querySelector("[data-row-hint]");

        var productId = productInput.value.trim();
        var quantity = parseInt(quantityInput.value, 10);
        var product = products[productId];

        hintEl.classList.remove("warn");

        if (!productId) {
            lineTotalEl.textContent = "—";
            hintEl.textContent = "";
            return { subtotal: 0, tax: 0 };
        }

        if (!product) {
            lineTotalEl.textContent = "—";
            hintEl.textContent = "Unknown product ID -- check the suggestions list.";
            hintEl.classList.add("warn");
            return { subtotal: 0, tax: 0 };
        }

        hintEl.textContent = product.name + " · Rs. " + product.price.toFixed(2) + " each · " + product.stock + " in stock";

        if (!quantity || quantity <= 0) {
            lineTotalEl.textContent = "—";
            return { subtotal: 0, tax: 0 };
        }

        if (quantity > product.stock) {
            hintEl.textContent = "Only " + product.stock + " in stock -- reduce the quantity.";
            hintEl.classList.add("warn");
        }

        var lineSubtotal = round2(product.price * quantity);
        var lineTax = round2((lineSubtotal * product.taxPercent) / 100);
        var lineTotal = round2(lineSubtotal + lineTax);
        lineTotalEl.textContent = formatMoney(lineTotal);

        return { subtotal: lineSubtotal, tax: lineTax };
    }

    function totalReceived() {
        var total = 0;
        document.querySelectorAll("[data-tender-input]").forEach(function (input) {
            var count = parseInt(input.value, 10);
            var value = parseFloat(input.getAttribute("data-denom-value"));
            if (count > 0) {
                total += count * value;
            }
        });
        return total;
    }

    function updateSummary() {
        var subtotal = 0;
        var tax = 0;

        container.querySelectorAll("[data-row]").forEach(function (row) {
            var totals = updateRow(row);
            subtotal += totals.subtotal;
            tax += totals.tax;
        });

        subtotal = round2(subtotal);
        tax = round2(tax);
        var net = round2(subtotal + tax);
        var amountToCollect = Math.floor(net);

        document.querySelector("[data-sum-subtotal]").textContent = formatMoney(subtotal);
        document.querySelector("[data-sum-tax]").textContent = formatMoney(tax);
        document.querySelector("[data-sum-total]").textContent = formatMoney(amountToCollect, 0);

        var changeRow = document.querySelector("[data-change-row]");
        var changeLabel = document.querySelector("[data-change-label]");
        var changeValue = document.querySelector("[data-change-value]");
        var received = totalReceived();

        if (!changeRow) {
            return;
        }

        if (received === 0 || amountToCollect === 0) {
            changeRow.style.display = "none";
            return;
        }

        changeRow.style.display = "";
        var diff = round2(received - amountToCollect);
        if (diff >= 0) {
            changeRow.classList.remove("short");
            changeLabel.textContent = "Change to give";
            changeValue.textContent = formatMoney(diff, 0);
        } else {
            changeRow.classList.add("short");
            changeLabel.textContent = "Still short by";
            changeValue.textContent = formatMoney(-diff, 0);
        }
    }

    if (addButton && template) {
        addButton.addEventListener("click", function () {
            container.appendChild(template.content.cloneNode(true));
            updateSummary();
        });
    }

    container.addEventListener("click", function (event) {
        if (event.target.closest(".remove-row")) {
            event.target.closest("[data-row]").remove();
            updateSummary();
        }
    });

    container.addEventListener("input", updateSummary);
    document.addEventListener("input", function (event) {
        if (event.target.matches("[data-tender-input]")) {
            updateSummary();
        }
    });

    /* ---------- Catalog: search filter + click-to-add ---------- */

    function addOrFillRow(productId) {
        var rows = container.querySelectorAll("[data-row]");
        var targetRow = null;
        rows.forEach(function (row) {
            if (!targetRow && row.querySelector("[data-product-input]").value.trim() === "") {
                targetRow = row;
            }
        });
        if (!targetRow && template) {
            container.appendChild(template.content.cloneNode(true));
            var allRows = container.querySelectorAll("[data-row]");
            targetRow = allRows[allRows.length - 1];
        }
        if (!targetRow) {
            return;
        }
        var productInput = targetRow.querySelector("[data-product-input]");
        var quantityInput = targetRow.querySelector("[data-quantity-input]");
        productInput.value = productId;
        if (!quantityInput.value) {
            quantityInput.value = "1";
        }
        updateSummary();
        quantityInput.focus();
        quantityInput.select();
    }

    if (catalogTable) {
        catalogTable.addEventListener("click", function (event) {
            var row = event.target.closest(".product-pick-row");
            if (row) {
                addOrFillRow(row.getAttribute("data-pick-product-id"));
            }
        });
    }

    if (filterInput && catalogTable) {
        filterInput.addEventListener("input", function () {
            var query = filterInput.value.trim().toLowerCase();
            catalogTable.querySelectorAll(".product-pick-row").forEach(function (row) {
                var haystack = row.getAttribute("data-search-text") || "";
                row.style.display = haystack.indexOf(query) === -1 ? "none" : "";
            });
        });
    }

    updateSummary();
});
