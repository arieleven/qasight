import { defineConfig } from "allure";

export default defineConfig({
  name: "QASight Report",
  output: "./reports/allure",
  historyPath: "./.testo/allure-history.jsonl",
  appendHistory: true,
  plugins: {
    awesome: {
      options: {
        singleFile: false,
        reportLanguage: "en",
        open: false,
      },
    },
  },
});
