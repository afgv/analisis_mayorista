#!/usr/bin/env node
/**
 * Valida un tema de Power BI contra el esquema JSON oficial de Microsoft.
 * Es la MISMA validación que hace Power BI Desktop al importar (nombres Y tipos),
 * así que si esto pasa, el archivo importa.
 *
 *   npm install ajv@8
 *   node validar_tema.js tema_radar_mayorista.json
 *   node validar_tema.js tema_radar_mayorista.json 2.137          (fijar una versión)
 *   node validar_tema.js tema_radar_mayorista.json 2.128,2.156    (varias)
 *
 * Los esquemas se descargan de microsoft/powerbi-desktop-samples y se cachean
 * en ./.schemas/. Sin internet, usa lo que ya esté cacheado.
 *
 * Además del archivo tal cual, valida el bloque global visualStyles["*"] contra
 * la definición de un visual concreto (barChart). El esquema no tipa ese bloque
 * — acepta cualquier cosa — así que una propiedad mal escrita ahí no rompe la
 * importación pero se ignora en silencio, que es peor.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const https = require('https');

const VERSIONES_POR_DEFECTO = ['2.128', '2.137', '2.148', '2.156'];
const BASE = 'https://raw.githubusercontent.com/microsoft/powerbi-desktop-samples/main/Report%20Theme%20JSON%20Schema/reportThemeSchema-';
const CACHE = path.join(__dirname, '.schemas');

const leerJson = f => JSON.parse(fs.readFileSync(f, 'utf8').replace(/^﻿/, ''));

function bajar(url, destino) {
  return new Promise((resolve, reject) => {
    https.get(url, res => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        return bajar(res.headers.location, destino).then(resolve, reject);
      }
      if (res.statusCode !== 200) return reject(new Error('HTTP ' + res.statusCode));
      const chunks = [];
      res.on('data', c => chunks.push(c));
      res.on('end', () => { fs.writeFileSync(destino, Buffer.concat(chunks)); resolve(destino); });
    }).on('error', reject);
  });
}

async function obtenerEsquema(version) {
  if (!fs.existsSync(CACHE)) fs.mkdirSync(CACHE, { recursive: true });
  const destino = path.join(CACHE, `reportThemeSchema-${version}.json`);
  if (!fs.existsSync(destino)) {
    await bajar(BASE + version + '.json', destino);
  }
  return leerJson(destino);
}

function formatearErrores(errores) {
  return errores.map(e => {
    const ruta = e.instancePath || '(raíz)';
    let detalle = e.message;
    if (e.keyword === 'additionalProperties') detalle = `propiedad no permitida "${e.params.additionalProperty}"`;
    if (e.keyword === 'enum') detalle = `valor inválido — permitidos: ${JSON.stringify(e.params.allowedValues)}`;
    if (e.keyword === 'const') detalle = `debe ser ${JSON.stringify(e.params.allowedValue)}`;
    return `  X ${ruta}: ${detalle}`;
  });
}

// ---------------------------------------------------------------------------
// ajv verifica tipos y valores, pero los objetos de cada tarjeta no declaran
// additionalProperties:false, asi que una propiedad con el nombre mal escrito
// pasa la validacion y despues Power BI la ignora en silencio. Este chequeo
// compara los nombres contra las definiciones del esquema.
// ---------------------------------------------------------------------------
function revisarNombres(esquema, tema) {
  const avisos = [];
  const deref = r => r.split('/').slice(1).reduce((o, k) => o[k], esquema);
  const propsDe = def => {
    const out = {};
    const walk = d => {
      if (!d) return;
      if (d.$ref) return walk(deref(d.$ref));
      (d.allOf || []).forEach(walk);
      (d.anyOf || []).forEach(walk);
      if (d.properties) Object.assign(out, d.properties);
    };
    walk(def);
    return out;
  };
  const vs = esquema.properties.visualStyles.properties;

  for (const [visual, cuerpo] of Object.entries(tema.visualStyles || {})) {
    // el bloque global no esta tipado: se compara contra barChart, que tiene
    // todas las tarjetas comunes (title, border, ejes, leyenda, etiquetas)
    const nombreDef = visual === '*' ? 'barChart' : visual;
    const etiqueta = visual === '*' ? 'visualStyles["*"]' : 'visualStyles.' + visual;
    if (!vs[nombreDef]) { avisos.push(`${etiqueta}: visual desconocido`); continue; }
    const tarjetas = propsDe(vs[nombreDef].properties['*']);

    for (const [tarjeta, arr] of Object.entries(cuerpo['*'] || {})) {
      if (!tarjetas[tarjeta]) {
        if (visual !== '*') avisos.push(`${etiqueta}: tarjeta desconocida "${tarjeta}"`);
        continue;
      }
      const item = tarjetas[tarjeta].items ||
        (tarjetas[tarjeta].$ref ? deref(tarjetas[tarjeta].$ref).items : null);
      if (!item) continue;
      const permitidas = propsDe(item);
      if (!Object.keys(permitidas).length) continue;
      (arr || []).forEach(obj => Object.keys(obj).forEach(prop => {
        if (!permitidas[prop]) avisos.push(`${etiqueta}.${tarjeta}: propiedad desconocida "${prop}"`);
      }));
    }
  }
  return avisos;
}

// El esquema tipa cada visual por separado pero deja visualStyles["*"] sin tipar.
// Clonamos ese bloque bajo un visual real para que ajv sí lo revise.
function temaConGlobalTipado(tema, visual) {
  const global = tema.visualStyles && tema.visualStyles['*'] && tema.visualStyles['*']['*'];
  if (!global) return null;
  return { name: tema.name, visualStyles: { [visual]: { '*': global } } };
}

(async () => {
  const archivo = process.argv[2];
  if (!archivo) {
    console.error('uso: node validar_tema.js <tema.json> [version[,version...]]');
    process.exit(2);
  }
  const versiones = process.argv[3] ? process.argv[3].split(',') : VERSIONES_POR_DEFECTO;

  let Ajv;
  try { Ajv = require('ajv'); }
  catch { console.error('Falta ajv. Correr:  npm install ajv@8'); process.exit(2); }

  const tema = leerJson(archivo);
  let fallo = false;

  for (const v of versiones) {
    let esquema;
    try { esquema = await obtenerEsquema(v); }
    catch (e) { console.log(`\n--- esquema ${v}: no se pudo obtener (${e.message}) ---`); continue; }

    const ajv = new Ajv({ allErrors: true, strict: false, validateFormats: false, validateSchema: false });
    const validar = ajv.compile(esquema);

    console.log(`\n--- esquema ${v} (Power BI ${esquema.description || '?'}) ---`);

    if (validar(tema)) {
      console.log('  OK  el archivo pasa la validación de importación');
    } else {
      fallo = true;
      console.log('  FALLA — Power BI va a rechazar el archivo:');
      formatearErrores(validar.errors).forEach(l => console.log(l));
    }

    // revisión extra 1: tipos dentro del bloque global, que el esquema no tipa
    const sombra = temaConGlobalTipado(tema, 'barChart');
    if (sombra) {
      const validarSombra = ajv.compile(esquema);
      if (!validarSombra(sombra)) {
        console.log('  AVISO — tipos en visualStyles["*"] (no rompe la importación, se ignora en silencio):');
        formatearErrores(validarSombra.errors)
          .map(l => l.replace('/visualStyles/barChart/*/', '/visualStyles/*/*/'))
          .forEach(l => console.log(l.replace('  X ', '  ! ')));
      }
    }

    // revisión extra 2: nombres de propiedad, que ajv no puede chequear
    const avisos = revisarNombres(esquema, tema);
    if (avisos.length) {
      console.log('  AVISO — nombres desconocidos (no rompen la importación, se ignoran en silencio):');
      avisos.forEach(a => console.log('  ! ' + a));
    }
  }

  process.exit(fallo ? 1 : 0);
})();
