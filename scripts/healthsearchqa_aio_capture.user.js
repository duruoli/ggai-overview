// ==UserScript==
// @name         HealthSearchQA Google AI Overview Capture
// @namespace    https://github.com/healthsearchqa-aio-pilot
// @version      0.4.0
// @description  Automatically capture Google AI Overviews for any HealthSearchQA question set.
// @author       HealthSearchQA
// @include      /^https:\/\/(www\.)?google\.[^/]+\/search(?:[/?#].*)?$/
// @run-at       document-idle
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_deleteValue
// ==/UserScript==

(function () {
  'use strict';

  const SCRIPT_VERSION = '0.4.0';
  const SCHEMA_VERSION = '2.0.0';
  const EXPERIMENT_NAME = 'healthsearchqa_google_aio_general';
  // Change this to 2 (or more) only when the new collection protocol needs
  // repeated captures of the exact same query.
  const TARGET_RUNS_PER_QUERY = 1;
  const AUTO_CAPTURE_START_DELAY_MS = 3000;
  const AUTO_RETRY_DELAY_MS = 3000;
  const AUTO_RETRY_LIMIT = 2;
  const ABSENT_AIO_GRACE_PERIOD_MS = 12000;
  const AIO_READY_TIMEOUT_MS = 30000;
  const EXPANSION_TIMEOUT_MS = 15000;
  const TEXT_STABLE_PERIOD_MS = 1500;
  // A fresh namespace prevents records from the old fixed-40/redo workflow
  // from being mixed into a new question set.
  const STORAGE_PREFIX = 'hsqa-aio-capture:general:v2';
  const INDEX_KEY = `${STORAGE_PREFIX}:record-index`;
  const EXPERIMENT_ID_KEY = `${STORAGE_PREFIX}:experiment-id`;
  const RECORD_KEY_PREFIX = `${STORAGE_PREFIX}:record:`;
  const PANEL_HOST_ID = 'hsqa-aio-capture-host';

  // These are deliberately redundant. Google changes class names often, so the
  // semantic heading fallback below is the primary long-term safeguard.
  const AIO_ROOT_SELECTORS = [
    '[data-attrid="ai_overview"]',
    '[data-attrid^="ai_overview"]',
    '[data-attrid*="AIOverview"]',
    '[data-ly^="/desktop_aio_layout/"]',
    '[data-mcp]',
    '[data-mcpr]',
    '.M8OgIe',
    '.LT6XE',
  ];
  const PAGE_ROOT_IDS = new Set(['search', 'rso', 'main', 'center_col', 'rcnt']);
  const LOADING_PHRASES = [
    'generating an ai overview',
    'generating ai overview',
    'loading ai overview',
    '正在生成 ai 概览',
    '正在生成 ai 摘要',
  ];

  let ui = null;
  let captureInProgress = false;
  let lastObservedQuery = normalizeQuestion(getCurrentQuery());
  let autoCaptureGeneration = 0;

  function normalizeQuestion(value) {
    return String(value || '')
      .normalize('NFKC')
      .replace(/\\(['\u2019])/g, '$1')
      .replace(/[\u2018\u2019]/g, "'")
      .replace(/[\u201c\u201d]/g, '"')
      .replace(/\s+/g, ' ')
      .trim()
      .toLocaleLowerCase('en-US');
  }

  function getCurrentQuery() {
    return new URL(location.href).searchParams.get('q')?.trim() || '';
  }

  function queryKey(value) {
    return normalizeQuestion(value);
  }

  function makeQueryId(value) {
    // FNV-1a gives a short, deterministic identifier without exposing the full
    // question in storage keys. The question text remains in every record.
    let hash = 0x811c9dc5;
    for (const character of queryKey(value)) {
      hash ^= character.codePointAt(0);
      hash = Math.imul(hash, 0x01000193);
    }
    return `QUERY_${(hash >>> 0).toString(16).padStart(8, '0').toUpperCase()}`;
  }

  function sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  function storageGet(key, fallback) {
    try {
      const value = GM_getValue(key, fallback);
      return value === undefined ? fallback : value;
    } catch (error) {
      throw new Error(`Could not read Tampermonkey storage: ${error.message}`);
    }
  }

  function storageSet(key, value) {
    try {
      GM_setValue(key, value);
    } catch (error) {
      throw new Error(`Could not write Tampermonkey storage: ${error.message}`);
    }
  }

  function storageDelete(key) {
    try {
      GM_deleteValue(key);
    } catch (error) {
      throw new Error(`Could not delete from Tampermonkey storage: ${error.message}`);
    }
  }

  function getIndex() {
    const value = storageGet(INDEX_KEY, []);
    return Array.isArray(value) ? value : [];
  }

  function makeId() {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function getExperimentId() {
    let id = storageGet(EXPERIMENT_ID_KEY, '');
    if (!id) {
      id = makeId();
      storageSet(EXPERIMENT_ID_KEY, id);
    }
    return id;
  }

  function readAllRecords() {
    return getIndex()
      .map((entry) => storageGet(`${RECORD_KEY_PREFIX}${entry.id}`, null))
      .filter(Boolean);
  }

  function recordQueryKey(record) {
    return queryKey(record?.query_key || record?.question || record?.query_from_url || '');
  }

  function getRunPlan(records, question) {
    const normalized = queryKey(question);
    if (!normalized) return null;
    const questionRecords = records
      .filter((record) => recordQueryKey(record) === normalized)
      .sort((a, b) => a.run - b.run);
    for (let run = 1; run <= TARGET_RUNS_PER_QUERY; run += 1) {
      if (!questionRecords.some((record) => record.run === run)) return { run };
    }
    return null;
  }

  function saveRecord(record) {
    const id = makeId();
    const key = `${RECORD_KEY_PREFIX}${id}`;
    const index = getIndex();
    storageSet(key, record);
    try {
      storageSet(INDEX_KEY, [...index, { id, captured_at: record.timestamp }]);
    } catch (error) {
      storageDelete(key);
      throw error;
    }
  }

  function nextSampleOrder(records, question) {
    const normalized = queryKey(question);
    const existing = records.find((record) => recordQueryKey(record) === normalized);
    if (existing?.sample_order) return existing.sample_order;
    return new Set(records.map(recordQueryKey).filter(Boolean)).size + 1;
  }

  function isVisible(element) {
    if (!(element instanceof Element)) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== 'none'
      && style.visibility !== 'hidden'
      && Number(style.opacity || 1) !== 0
      && rect.width > 0
      && rect.height > 0;
  }

  function compactText(value) {
    return String(value || '')
      .replace(/\u00a0/g, ' ')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/\n[ \t]+/g, '\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }

  function elementText(element) {
    return compactText(element?.innerText || element?.textContent || '');
  }

  function isAioLabelText(value) {
    return /^(?:ai overview(?:s)?|ai 概览)$/i.test(compactText(value));
  }

  function isAioLabel(element) {
    if (!isVisible(element)) return false;
    const aria = compactText(element.getAttribute('aria-label') || '');
    const text = elementText(element);
    return isAioLabelText(aria) || (isAioLabelText(text) && text.length < 40);
  }

  function externalLinkCount(element) {
    return [...element.querySelectorAll('a[href]')]
      .filter((anchor) => {
        try {
          const url = new URL(anchor.href, location.href);
          return url.protocol === 'http:' || url.protocol === 'https:';
        } catch {
          return false;
        }
      }).length;
  }

  function candidateScore(element, source) {
    if (!isVisible(element)) return Number.NEGATIVE_INFINITY;
    const textLength = elementText(element).length;
    if (textLength < 40 || textLength > 60000) return Number.NEGATIVE_INFINITY;

    let score = source === 'selector' ? 150 : 50;
    score += Math.min(externalLinkCount(element), 12) * 4;
    score += textLength >= 100 && textLength <= 20000 ? 35 : 0;
    score += /^AI Overview/i.test(elementText(element)) ? 20 : 0;
    score -= Math.log10(Math.max(textLength, 1)) * 5;
    if (PAGE_ROOT_IDS.has(element.id) || ['MAIN', 'BODY', 'HTML'].includes(element.tagName)) score -= 300;
    if (element.querySelectorAll('[data-hveid], [data-ved]').length > 100) score -= 80;
    return score;
  }

  function findAioContainer() {
    const candidates = [];
    for (const selector of AIO_ROOT_SELECTORS) {
      for (const element of document.querySelectorAll(selector)) {
        candidates.push({ element, source: 'selector', selector });
      }
    }

    // Avoid measuring every div on the page. First collect likely accessibility
    // labels, then add elements whose own direct text node is exactly the label.
    const possibleLabels = new Set(document.querySelectorAll(
      'h1, h2, h3, h4, [role="heading"], [aria-label*="AI Overview" i], [aria-label*="AI 概览"]',
    ));
    const textWalker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let textNode = textWalker.nextNode();
    while (textNode) {
      if (isAioLabelText(textNode.nodeValue)) {
        possibleLabels.add(textNode.parentElement);
      }
      textNode = textWalker.nextNode();
    }
    for (const label of possibleLabels) {
      if (!label) continue;
      if (!isAioLabel(label)) continue;
      let current = label.parentElement;
      let depth = 0;
      while (current && depth < 8) {
        if (PAGE_ROOT_IDS.has(current.id) || ['MAIN', 'BODY', 'HTML'].includes(current.tagName)) break;
        candidates.push({ element: current, source: 'heading', selector: 'semantic:AI Overview' });
        current = current.parentElement;
        depth += 1;
      }
    }

    const unique = new Map();
    for (const candidate of candidates) {
      const score = candidateScore(candidate.element, candidate.source);
      const previous = unique.get(candidate.element);
      if (!previous || score > previous.score) unique.set(candidate.element, { ...candidate, score });
    }
    return [...unique.values()].sort((a, b) => b.score - a.score)[0] || null;
  }

  function pageShowsAioLoading() {
    const bodyText = String(document.body?.innerText || '').toLocaleLowerCase('en-US');
    return LOADING_PHRASES.some((phrase) => bodyText.includes(phrase));
  }

  async function waitForAioState(
    timeoutMs = AIO_READY_TIMEOUT_MS,
    absentGracePeriodMs = ABSENT_AIO_GRACE_PERIOD_MS,
  ) {
    const startedAt = Date.now();
    const deadline = Date.now() + timeoutMs;
    let candidate = findAioContainer();
    while (Date.now() < deadline) {
      const textLength = candidate ? elementText(candidate.element).length : 0;
      if (candidate && textLength >= 80 && !pageShowsAioLoading()) return candidate;
      if (!candidate
          && Date.now() - startedAt >= absentGracePeriodMs
          && !pageShowsAioLoading()) return null;
      await sleep(500);
      candidate = findAioContainer();
    }
    return candidate;
  }

  function showMoreLabel(element) {
    return compactText(
      element.getAttribute('aria-label')
      || element.getAttribute('title')
      || element.innerText
      || element.textContent,
    );
  }

  function isAioShowMoreLabel(value) {
    return /^(?:show more(?: ai overview)?|显示更多(?: ai 概览)?|展开更多)$/i.test(
      compactText(value),
    );
  }

  function findAioShowMoreControls(container) {
    if (!container) return [];
    return [...container.querySelectorAll('button, [role="button"], [aria-label], [jsname="rPRdsc"]')]
      .filter((element) => isVisible(element) && isAioShowMoreLabel(showMoreLabel(element)));
  }

  async function waitForStableExpandedAio(timeoutMs = EXPANSION_TIMEOUT_MS) {
    const deadline = Date.now() + timeoutMs;
    let lastText = '';
    let stableSince = 0;
    let latestCandidate = findAioContainer();

    while (Date.now() < deadline) {
      latestCandidate = findAioContainer();
      if (!latestCandidate) {
        await sleep(250);
        continue;
      }
      const controls = findAioShowMoreControls(latestCandidate.element);
      const text = elementText(latestCandidate.element);
      if (!controls.length && text.length >= 80 && !pageShowsAioLoading()) {
        if (text === lastText) {
          if (!stableSince) stableSince = Date.now();
          if (Date.now() - stableSince >= TEXT_STABLE_PERIOD_MS) return latestCandidate;
        } else {
          lastText = text;
          stableSince = Date.now();
        }
      } else {
        stableSince = 0;
      }
      await sleep(250);
    }
    return latestCandidate;
  }

  async function expandAioFully(initialCandidate) {
    let candidate = initialCandidate;
    let clickCount = 0;
    const beforeCharacterCount = elementText(candidate?.element).length;

    for (let pass = 0; pass < 3; pass += 1) {
      const controls = findAioShowMoreControls(candidate?.element);
      if (!controls.length) break;
      controls[0].scrollIntoView({ block: 'center', behavior: 'auto' });
      controls[0].click();
      clickCount += 1;
      candidate = await waitForStableExpandedAio();
    }

    candidate = await waitForStableExpandedAio();
    const remainingControls = findAioShowMoreControls(candidate?.element);
    const afterCharacterCount = elementText(candidate?.element).length;
    return {
      candidate,
      click_count: clickCount,
      before_character_count: beforeCharacterCount,
      after_character_count: afterCharacterCount,
      expansion_complete: remainingControls.length === 0,
      remaining_control_count: remainingControls.length,
    };
  }

  function unwrapGoogleUrl(rawHref) {
    try {
      const url = new URL(rawHref, location.href);
      const googleHost = /^(?:www\.)?google\.[a-z.]+$/i.test(url.hostname);
      if (googleHost && url.pathname === '/url') {
        const target = url.searchParams.get('q') || url.searchParams.get('url');
        if (target) return new URL(target).href;
      }
      if (googleHost || url.hostname === 'vertexaisearch.cloud.google.com') {
        for (const key of ['url', 'q']) {
          const embedded = url.searchParams.get(key);
          if (/^https?:\/\//i.test(embedded || '')) return new URL(embedded).href;
        }
      }
      return url.href;
    } catch {
      return '';
    }
  }

  function isCitationUrl(url) {
    try {
      const parsed = new URL(url);
      if (!['http:', 'https:'].includes(parsed.protocol)) return false;
      const isGoogle = /(^|\.)google\.[a-z.]+$/i.test(parsed.hostname);
      const isGroundingRedirect = parsed.hostname === 'vertexaisearch.cloud.google.com';
      return !isGoogle || isGroundingRedirect;
    } catch {
      return false;
    }
  }

  function citationTitle(anchor) {
    const direct = compactText(
      anchor.getAttribute('aria-label')
      || anchor.getAttribute('title')
      || anchor.innerText
      || anchor.textContent,
    );
    if (direct && !/^\d+$/.test(direct)) return direct;

    let parent = anchor.parentElement;
    for (let depth = 0; parent && depth < 3; depth += 1, parent = parent.parentElement) {
      const text = elementText(parent);
      if (text && text.length <= 300) return text;
    }
    return direct;
  }

  function extractCitations(container) {
    const seen = new Set();
    const citations = [];
    for (const anchor of container.querySelectorAll('a[href]')) {
      const url = unwrapGoogleUrl(anchor.href);
      if (!url || !isCitationUrl(url)) continue;
      const normalizedUrl = url.replace(/#.*$/, '');
      if (seen.has(normalizedUrl)) continue;
      seen.add(normalizedUrl);
      let domain = '';
      try {
        domain = new URL(normalizedUrl).hostname.replace(/^www\./i, '');
      } catch {
        // Keep a blank domain; the original href remains available for auditing.
      }
      citations.push({
        position: citations.length + 1,
        title: citationTitle(anchor),
        url: normalizedUrl,
        domain,
        anchor_text: compactText(anchor.innerText || anchor.textContent || ''),
        raw_href: anchor.getAttribute('href') || '',
      });
    }
    return citations;
  }

  const SANITIZED_HTML_ATTRIBUTES = new Set([
    'alt',
    'aria-hidden',
    'aria-label',
    'class',
    'data-attrid',
    'data-container-id',
    'data-ep-type',
    'data-ly',
    'data-mcp',
    'data-mcpr',
    'data-scope-id',
    'data-subtree',
    'dir',
    'href',
    'id',
    'jsname',
    'lang',
    'rel',
    'role',
    'target',
    'title',
  ]);

  function sanitizeAioHtml(container) {
    if (!container) return '';
    const clone = container.cloneNode(true);

    // Remove executable/presentation content and image payloads. These account
    // for most of Google's raw DOM size but are not useful for later auditing.
    clone.querySelectorAll('script, style, noscript, template, svg, canvas, iframe, object, picture, source, img')
      .forEach((element) => element.remove());

    // Remove nodes explicitly hidden with HTML/inline styles. CSS-class-based
    // visibility cannot be reconstructed after stylesheets have been removed.
    clone.querySelectorAll('*').forEach((element) => {
      const inlineStyle = element.getAttribute('style') || '';
      if (element.hasAttribute('hidden')
          || element.getAttribute('aria-hidden') === 'true'
          || /display\s*:\s*none/i.test(inlineStyle)
          || /visibility\s*:\s*hidden/i.test(inlineStyle)) {
        element.remove();
      }
    });

    const commentWalker = clone.ownerDocument.createTreeWalker(clone, NodeFilter.SHOW_COMMENT);
    const comments = [];
    let comment = commentWalker.nextNode();
    while (comment) {
      comments.push(comment);
      comment = commentWalker.nextNode();
    }
    comments.forEach((node) => node.remove());

    for (const element of [clone, ...clone.querySelectorAll('*')]) {
      for (const attribute of [...element.attributes]) {
        if (!SANITIZED_HTML_ATTRIBUTES.has(attribute.name)) {
          element.removeAttribute(attribute.name);
        }
      }
      const href = element.getAttribute('href');
      if (href && /^javascript:/i.test(href.trim())) element.removeAttribute('href');
    }

    return clone.outerHTML;
  }

  function sanitizeLegacyHtml(rawHtml) {
    if (!rawHtml) return '';
    const parsed = new DOMParser().parseFromString(rawHtml, 'text/html');
    return sanitizeAioHtml(parsed.body.firstElementChild);
  }

  function prepareRecordForExport(record) {
    const prepared = { ...record };
    if (prepared.aio_outer_html) {
      const rawLength = prepared.aio_outer_html.length;
      prepared.aio_html_sanitized = sanitizeLegacyHtml(prepared.aio_outer_html);
      delete prepared.aio_outer_html;
      prepared.extraction = {
        ...prepared.extraction,
        raw_dom_character_count: rawLength,
        sanitized_dom_character_count: prepared.aio_html_sanitized.length,
      };
    }
    return prepared;
  }

  function localIsoTimestamp(date = new Date()) {
    const pad = (value) => String(value).padStart(2, '0');
    const offsetMinutes = -date.getTimezoneOffset();
    const sign = offsetMinutes >= 0 ? '+' : '-';
    const absoluteOffset = Math.abs(offsetMinutes);
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
      + `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
      + `.${String(date.getMilliseconds()).padStart(3, '0')}`
      + `${sign}${pad(Math.floor(absoluteOffset / 60))}:${pad(absoluteOffset % 60)}`;
  }

  function updateMessage(message, tone = 'neutral') {
    if (!ui) return;
    ui.message.textContent = message;
    ui.message.dataset.tone = tone;
  }

  function setButtonsDisabled(disabled) {
    if (!ui) return;
    for (const button of [ui.capture, ui.exportAll, ui.undo, ui.reset]) button.disabled = disabled;
  }

  function updatePanel() {
    if (!ui) return;
    const records = readAllRecords();
    const question = getCurrentQuery();
    const questionCount = new Set(records.map(recordQueryKey).filter(Boolean)).size;
    ui.progress.textContent = `Questions: ${questionCount} · Captures: ${records.length}`;
    ui.exportAll.disabled = records.length === 0 || captureInProgress;
    ui.undo.disabled = records.length === 0 || captureInProgress;
    ui.reset.disabled = records.length === 0 || captureInProgress;

    if (!queryKey(question)) {
      ui.current.textContent = 'Open a Google results page with a query';
      ui.capture.disabled = true;
      return;
    }

    const plan = getRunPlan(records, question);
    if (!plan) {
      ui.current.textContent = `${makeQueryId(question)} · captured`;
      ui.capture.disabled = true;
      return;
    }
    ui.current.textContent = `${makeQueryId(question)} · auto run ${plan.run}/${TARGET_RUNS_PER_QUERY}`;
    ui.capture.disabled = captureInProgress;
  }

  async function captureCurrentPage(options = {}) {
    if (captureInProgress) return { status: 'busy' };
    const automatic = options.automatic === true;
    const question = getCurrentQuery();
    const normalizedQuestion = queryKey(question);
    if (!normalizedQuestion) {
      updateMessage('Not saved: the current Google search has no query.', 'error');
      return { status: 'ignored' };
    }

    const existingRecords = readAllRecords();
    const runPlan = getRunPlan(existingRecords, question);
    if (!runPlan) {
      updateMessage('No capture needed: this query is already stored.', 'success');
      return { status: 'complete' };
    }

    if (!document.querySelector('#search, #rso, main')) {
      updateMessage('Not saved: Google results do not appear to be loaded yet.', 'error');
      return { status: 'failed' };
    }

    captureInProgress = true;
    setButtonsDisabled(true);
    updateMessage(`${automatic ? 'Auto capture' : 'Manual retry'}: waiting for the AI Overview…`);

    try {
      let candidate = await waitForAioState();
      if (queryKey(getCurrentQuery()) !== normalizedQuestion) {
        throw new Error('The search query changed while capture was waiting.');
      }

      let expansion = {
        click_count: 0,
        before_character_count: 0,
        after_character_count: 0,
        expansion_complete: true,
        remaining_control_count: 0,
      };
      if (candidate) {
        const showMoreCount = findAioShowMoreControls(candidate.element).length;
        updateMessage(showMoreCount
          ? 'AI Overview found. Expanding “Show more” before capture…'
          : 'AI Overview found. Waiting for the text to stabilize…');
        expansion = await expandAioFully(candidate);
        candidate = expansion.candidate;
        if (!expansion.expansion_complete) {
          throw new Error('The AI Overview “Show more” control could not be fully expanded. Nothing was saved.');
        }
      }

      if (queryKey(getCurrentQuery()) !== normalizedQuestion) {
        throw new Error('The search query changed while the AI Overview was expanding.');
      }

      const container = candidate?.element || null;
      const aiOverviewText = container ? elementText(container) : '';
      if (container && /(?:^|\n)Show more(?:\n|$)/i.test(aiOverviewText)) {
        throw new Error('The extracted text still contains an unexpanded “Show more”. Nothing was saved.');
      }
      const citations = container ? extractCitations(container) : [];
      const rawDomCharacterCount = container?.outerHTML.length || 0;
      const sanitizedHtml = container ? sanitizeAioHtml(container) : '';
      const markerFound = Boolean(candidate);
      if (markerFound && aiOverviewText.length < 40) {
        throw new Error('An AI Overview marker was found, but its text was too short to save safely.');
      }
      const extractionStatus = markerFound ? 'success' : 'not_present';
      const now = new Date();
      const currentQuery = getCurrentQuery();
      const queryId = makeQueryId(question);
      const record = {
        schema_version: SCHEMA_VERSION,
        experiment_name: EXPERIMENT_NAME,
        experiment_id: getExperimentId(),
        dataset_id: queryId,
        query_id: queryId,
        query_key: normalizedQuestion,
        sample_order: nextSampleOrder(existingRecords, question),
        question,
        query_from_url: currentQuery,
        query_match: queryKey(currentQuery) === normalizedQuestion,
        run: runPlan.run,
        timestamp: localIsoTimestamp(now),
        timezone_offset_minutes: -now.getTimezoneOffset(),
        search_url: location.href,
        page_title: document.title,
        ai_overview_present: markerFound,
        ai_overview_text: aiOverviewText,
        citations,
        aio_html_sanitized: sanitizedHtml,
        extraction_status: extractionStatus,
        extraction: {
          script_version: SCRIPT_VERSION,
          strategy: candidate?.source || 'no_aio_marker_or_container_found',
          matched_selector: candidate?.selector || null,
          candidate_score: candidate ? Number(candidate.score.toFixed(2)) : null,
          extracted_character_count: aiOverviewText.length,
          extracted_citation_count: citations.length,
          capture_mode: automatic ? 'automatic' : 'manual_retry',
          show_more_click_count: expansion.click_count,
          pre_expansion_character_count: expansion.before_character_count,
          post_expansion_character_count: expansion.after_character_count,
          expansion_complete: expansion.expansion_complete,
          remaining_show_more_control_count: expansion.remaining_control_count,
          raw_dom_character_count: rawDomCharacterCount,
          sanitized_dom_character_count: sanitizedHtml.length,
          page_reported_aio_loading: pageShowsAioLoading(),
        },
        browser: {
          user_agent: navigator.userAgent,
          language: navigator.language,
        },
      };

      saveRecord(record);
      const presence = record.ai_overview_present
        ? `AIO text: ${aiOverviewText.length.toLocaleString()} characters · Citations: ${citations.length}`
        : 'No AI Overview detected; absence saved.';
      updateMessage(`Saved ${queryId} run ${record.run}. ${presence}`, 'success');
      return { status: 'saved', record };
    } catch (error) {
      console.error('[HSQA AIO Capture]', error);
      updateMessage(`Capture failed and was not saved: ${error.message}`, 'error');
      return { status: 'failed', error };
    } finally {
      captureInProgress = false;
      setButtonsDisabled(false);
      updatePanel();
    }
  }

  function makeExportPayload(records) {
    const sortedRecords = [...records]
      .sort((a, b) => a.sample_order - b.sample_order || a.run - b.run)
      .map(prepareRecordForExport);
    const questionsByKey = new Map();
    for (const record of sortedRecords) {
      const key = recordQueryKey(record);
      if (!key || questionsByKey.has(key)) continue;
      questionsByKey.set(key, {
        sample_order: record.sample_order,
        query_id: record.query_id || record.dataset_id || makeQueryId(record.question),
        question: record.question || record.query_from_url,
      });
    }
    const progress = [...questionsByKey.entries()]
      .map(([key, question]) => {
        const runs = sortedRecords
          .filter((record) => recordQueryKey(record) === key)
          .map((record) => record.run)
          .sort((a, b) => a - b);
        return {
          ...question,
          captured_runs: runs,
          complete: runs.length >= TARGET_RUNS_PER_QUERY,
        };
      })
      .sort((a, b) => a.sample_order - b.sample_order);
    return {
      schema_version: SCHEMA_VERSION,
      export_timestamp: localIsoTimestamp(),
      experiment: {
        name: EXPERIMENT_NAME,
        experiment_id: getExperimentId(),
        script_version: SCRIPT_VERSION,
        dataset: 'HealthSearchQA',
        question_set_mode: 'open_query_collection',
        question_count: progress.length,
        target_runs_per_query: TARGET_RUNS_PER_QUERY,
        actual_capture_count: sortedRecords.length,
      },
      record_summaries: sortedRecords.map((record) => ({
        dataset_id: record.dataset_id,
        query_id: record.query_id || record.dataset_id,
        sample_order: record.sample_order,
        run: record.run,
        ai_overview_present: record.ai_overview_present,
        extraction_status: record.extraction_status,
        text_character_count: record.ai_overview_text?.length || 0,
        citation_count: record.citations?.length || 0,
        text_preview: record.ai_overview_text?.slice(0, 240) || '',
      })),
      records: sortedRecords,
      progress,
    };
  }

  function exportAll() {
    try {
      const records = readAllRecords();
      if (!records.length) {
        updateMessage('Nothing to export yet.', 'warning');
        return;
      }
      const payload = makeExportPayload(records);
      const blob = new Blob([`${JSON.stringify(payload, null, 2)}\n`], { type: 'application/json' });
      const objectUrl = URL.createObjectURL(blob);
      const date = new Date().toISOString().slice(0, 10);
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = `healthsearchqa_aio_capture_general_${date}.json`;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      updateMessage(`Exported ${records.length} record${records.length === 1 ? '' : 's'}.`, 'success');
    } catch (error) {
      console.error('[HSQA AIO Capture]', error);
      updateMessage(`Export failed: ${error.message}`, 'error');
    }
  }

  function undoLast() {
    const index = getIndex();
    const last = index[index.length - 1];
    if (!last) {
      updateMessage('Nothing to undo.', 'warning');
      return;
    }
    const record = storageGet(`${RECORD_KEY_PREFIX}${last.id}`, null);
    const label = record ? `${record.query_id || record.dataset_id} run ${record.run}` : 'the last record';
    if (!window.confirm(`Delete ${label}? This cannot be undone after leaving the page.`)) return;
    storageSet(INDEX_KEY, index.slice(0, -1));
    storageDelete(`${RECORD_KEY_PREFIX}${last.id}`);
    updateMessage(`Deleted ${label}.`, 'success');
    updatePanel();
  }

  function resetAll() {
    const index = getIndex();
    if (!index.length) return;
    if (!window.confirm(`Delete all ${index.length} locally stored capture records? Export first if needed.`)) return;
    storageSet(INDEX_KEY, []);
    for (const entry of index) storageDelete(`${RECORD_KEY_PREFIX}${entry.id}`);
    storageDelete(EXPERIMENT_ID_KEY);
    updateMessage('All local experiment data were deleted.', 'success');
    updatePanel();
  }

  function addButton(container, label, className, handler) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = className;
    button.textContent = label;
    button.addEventListener('click', handler);
    container.append(button);
    return button;
  }

  function installPanel() {
    if (document.getElementById(PANEL_HOST_ID)) return;
    const host = document.createElement('div');
    host.id = PANEL_HOST_ID;
    document.documentElement.append(host);
    const shadow = host.attachShadow({ mode: 'closed' });
    shadow.innerHTML = `
      <style>
        :host { all: initial; }
        .panel {
          position: fixed; right: 18px; bottom: 18px; z-index: 2147483647;
          width: 300px; box-sizing: border-box; padding: 14px;
          color: #202124; background: rgba(255, 255, 255, .98);
          border: 1px solid #dadce0; border-radius: 12px;
          box-shadow: 0 4px 18px rgba(60, 64, 67, .28);
          font: 13px/1.4 Arial, sans-serif;
        }
        .title { font-size: 14px; font-weight: 700; margin-bottom: 2px; }
        .progress { font-weight: 700; color: #174ea6; }
        .current { margin: 6px 0 10px; color: #5f6368; overflow-wrap: anywhere; }
        .buttons { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; }
        button {
          appearance: none; padding: 8px 9px; border: 1px solid #dadce0;
          border-radius: 7px; color: #202124; background: #fff;
          font: 600 12px Arial, sans-serif; cursor: pointer;
        }
        button:hover:not(:disabled) { background: #f1f3f4; }
        button:disabled { cursor: not-allowed; opacity: .48; }
        button.primary { color: #fff; border-color: #1a73e8; background: #1a73e8; }
        button.primary:hover:not(:disabled) { background: #1765cc; }
        button.danger { color: #b3261e; }
        .message {
          min-height: 34px; margin-top: 10px; padding-top: 8px;
          border-top: 1px solid #eee; color: #5f6368; overflow-wrap: anywhere;
        }
        .message[data-tone="success"] { color: #137333; }
        .message[data-tone="warning"] { color: #b06000; }
        .message[data-tone="error"] { color: #b3261e; }
      </style>
      <section class="panel" aria-label="HealthSearchQA AIO capture controls">
        <div class="title">HealthSearchQA AIO Capture · Auto ON</div>
        <div class="progress"></div>
        <div class="current"></div>
        <div class="buttons"></div>
        <div class="message" aria-live="polite">Ready.</div>
      </section>
    `;
    const buttons = shadow.querySelector('.buttons');
    ui = {
      progress: shadow.querySelector('.progress'),
      current: shadow.querySelector('.current'),
      message: shadow.querySelector('.message'),
      capture: addButton(buttons, 'Retry Now', 'primary', () => captureCurrentPage({ automatic: false })),
      exportAll: addButton(buttons, 'Export All', '', exportAll),
      undo: addButton(buttons, 'Undo Last', '', undoLast),
      reset: addButton(buttons, 'Reset', 'danger', resetAll),
    };
    updatePanel();
  }

  function scheduleAutoCapture() {
    const generation = ++autoCaptureGeneration;
    const question = getCurrentQuery();
    const normalizedQuestion = queryKey(question);
    if (!normalizedQuestion) {
      updatePanel();
      return;
    }
    const plan = getRunPlan(readAllRecords(), question);
    if (!plan) {
      updateMessage('This query is already stored.', 'success');
      updatePanel();
      return;
    }

    const queryId = makeQueryId(question);
    updateMessage(`Auto capture scheduled for ${queryId} run ${plan.run}. Waiting for Google to load…`);
    let retryCount = 0;
    const attempt = async () => {
      if (generation !== autoCaptureGeneration) return;
      if (queryKey(getCurrentQuery()) !== normalizedQuestion) return;
      if (captureInProgress) {
        window.setTimeout(attempt, 500);
        return;
      }
      const result = await captureCurrentPage({ automatic: true });
      if (result?.status === 'failed'
          && generation === autoCaptureGeneration
          && queryKey(getCurrentQuery()) === normalizedQuestion
          && retryCount < AUTO_RETRY_LIMIT) {
        retryCount += 1;
        updateMessage(`Auto capture retry ${retryCount}/${AUTO_RETRY_LIMIT} scheduled for ${queryId}…`, 'warning');
        window.setTimeout(attempt, AUTO_RETRY_DELAY_MS);
      }
    };
    window.setTimeout(attempt, AUTO_CAPTURE_START_DELAY_MS);
  }

  function watchGoogleNavigation() {
    const observer = new MutationObserver(() => {
      const currentQuery = normalizeQuestion(getCurrentQuery());
      if (currentQuery === lastObservedQuery) return;
      lastObservedQuery = currentQuery;
      window.setTimeout(() => {
        updatePanel();
        scheduleAutoCapture();
      }, 250);
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
    window.addEventListener('popstate', () => {
      lastObservedQuery = normalizeQuestion(getCurrentQuery());
      updatePanel();
      scheduleAutoCapture();
    });
  }

  // Test hook: harmless in Tampermonkey and useful for syntax/pure-function tests.
  globalThis.__HSQA_AIO_CAPTURE_TEST__ = {
    normalizeQuestion,
    queryKey,
    makeQueryId,
    unwrapGoogleUrl,
    localIsoTimestamp,
    targetRunsPerQuery: TARGET_RUNS_PER_QUERY,
    recordQueryKey,
    getRunPlan,
    nextSampleOrder,
    makeExportPayload,
  };

  if (!globalThis.__HSQA_AIO_CAPTURE_SKIP_INIT__) {
    installPanel();
    watchGoogleNavigation();
    scheduleAutoCapture();
  }
})();
