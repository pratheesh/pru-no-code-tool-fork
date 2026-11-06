#!/usr/bin/env node
"use strict";
/*
 * Runs one cross-instance validator from the SHIPPED pru_blocks_static_module.syscfg.js
 * against a described design and prints the errors it logs.
 *
 * The register allocator is replaced by a table supplied in the spec, so a validator that
 * reads the allocation can be driven with an exact register assignment.
 *
 * Usage:
 *   node scripts/static_global_runner.js '<json>'
 *
 *   {
 *     "validator": "validateXfr2psiGlobal",
 *     "modules": { "<module path>": [ {"$name": "A", ...instance fields...}, ... ] },
 *     "order": ["A", "B"],                    // emitted instruction order
 *     "allocations": { "A": "R3" },           // getBlockRegisterAllocation() results
 *     "writers": [ {"module": "...", "label": "...", "crossCoreField": "...", "variableField": "..."} ]
 *   }
 *
 * An instance's port fields (output1, prev, next, input1) may name other instances as
 * {"inst": "<name>"}; they are resolved to the instance objects.
 * "writers" are appended to CROSS_CORE_WRITER_BLOCKS before the validator runs.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC_PATH = path.join(__dirname, "..", ".metadata", "sysconfig", ".meta", "pru_blocks",
    "common", "pru_blocks_static_module.syscfg.js");

function deepStub() {
    const fn = function () { return deepStub(); };
    return new Proxy(fn, {
        get(_t, prop) {
            if (prop === Symbol.toPrimitive || prop === "toString" || prop === "valueOf") return () => "";
            if (prop === Symbol.iterator || prop === "then" || prop === "length") return undefined;
            return deepStub();
        },
        apply() { return deepStub(); },
    });
}

function run(spec) {
    const byName = {};
    const modules = {};
    for (const mn of Object.keys(spec.modules || {})) {
        modules[mn] = { $instances: JSON.parse(JSON.stringify(spec.modules[mn])) };
        for (const i of modules[mn].$instances) byName[i.$name] = i;
    }
    for (const i of Object.values(byName)) {
        for (const port of ["prev", "next", "input1", "output1"]) {
            if (!Array.isArray(i[port])) continue;
            i[port] = i[port].map((e) => (e && typeof e.inst === "string" ? { inst: byName[e.inst] } : e));
        }
    }
    const order = spec.order || [];
    const allocations = spec.allocations || {};
    const allocator = {
        getPruRegisterAllocationSummary: () => ({
            pruInstructions: order.map(() => "nop"),
            pruInstructionModules: order.map((n) => {
                for (const mn of Object.keys(modules)) {
                    if (modules[mn].$instances.some((i) => i.$name === n)) return mn;
                }
                return "";
            }),
            pruInstructionInstances: order.map((n) => byName[n] || null),
            labels: [],
        }),
        getBlockRegisterAllocation: (n) => allocations[n] || "-",
    };
    const common = { getSocName: () => "am64x", getSelfSysCfgCoreName: () => "icss_g0_pru0" };
    const ctx = {
        system: {
            modules,
            getScript: (p) => {
                const s = String(p);
                if (s.includes("pru_register_allocator")) return allocator;
                if (s === "/common" || s.endsWith("/common")) return common;
                return deepStub();
            },
            deviceData: { device: "AM243x", package: "ZCZ" },
        },
        exports: {},
        module: { exports: {} },
        console: { log() {}, warn() {}, error() {} },
        require: () => deepStub(),
    };
    ctx.globalThis = ctx;
    vm.runInNewContext(
        fs.readFileSync(STATIC_PATH, "utf8") + "\n;globalThis.__v = " + spec.validator +
        ";globalThis.__w = CROSS_CORE_WRITER_BLOCKS;",
        ctx, { filename: "pru_blocks_static_module.syscfg.js" });
    for (const w of spec.writers || []) ctx.__w.push(w);
    const errors = [];
    ctx.__v({}, { logError: (m) => errors.push(String(m)), logWarning: () => {}, logInfo: () => {} });
    return errors;
}

if (require.main === module) {
    if (!process.argv[2]) {
        console.error("Usage: node scripts/static_global_runner.js '<json spec>'");
        process.exit(2);
    }
    console.log(JSON.stringify(run(JSON.parse(process.argv[2]))));
}

module.exports = { run };
