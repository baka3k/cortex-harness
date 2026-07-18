const express = require("express");
const app = express();

function listOrders(req, res) {
  res.json([]);
}

app.get("/orders", listOrders);
