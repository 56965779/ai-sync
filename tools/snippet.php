<?php
// SPDX-License-Identifier: MIT
// Copyright (c) 2026 金鑫

/*
 * ai-sync v3.2 Tool: Code Snippet Manager
 * snippet_set / snippet_get / snippet_list / snippet_delete
 */

function _snippet_dir($cfg) {
    return rtrim($cfg['WORK_DIR'], '/\\') . '/ai-sync/snippets/';
}

function _snippet_sanitize_name($name) {
    return preg_replace('/[^a-zA-Z0-9_.\-]/', '_', $name);
}

function action_snippet_set($req, $cfg) {
    global $E, $CONFIG;
    $name = $req['name'] ?? '';
    if ($name === '') return fail($E['INVALID_PARAM'], 'empty_name', 'name is required');
    $safe = _snippet_sanitize_name($name);
    $content = $req['content'] ?? '';
    if ($content === '') return fail($E['INVALID_PARAM'], 'empty_content', 'content is required');
    // tags 必须是字符串数组: 字符串会被 Python 端逐字符迭代, 对象/数字会写入异构记录,
    // 两端行为分叉且 AI 难以发现. 统一 1011.
    $tags = $req['tags'] ?? [];
    if ($tags === null) $tags = [];
    if (!is_array($tags)) {
        return fail($E['INVALID_PARAM'], 'bad_tags', 'tags 必须是字符串数组');
    }
    foreach ($tags as $__t) {
        if (!is_string($__t)) {
            return fail($E['INVALID_PARAM'], 'bad_tags', 'tags 必须是字符串数组');
        }
    }
    $tags = array_values($tags);
    $record = [
        'name'        => $name,
        'safe'        => $safe,
        'content'     => $content,
        'language'    => $req['language'] ?? 'plain',
        'tags'        => $tags,
        'description' => $req['description'] ?? '',
        'created_at'  => time(),
        'updated_at'  => time(),
    ];
    $dir = _snippet_dir($cfg);
    @mkdir($dir, 0755, true);
    $file = $dir . $safe . '.json';
    if (file_exists($file)) {
        $old = json_decode(file_get_contents($file), true) ?: [];
        $record['created_at'] = $old['created_at'] ?? time();
    }
    if (@file_put_contents($file, json_encode($record, JSON_UNESCAPED_UNICODE), LOCK_EX) === false) {
        return fail($E['UPLOAD_FAILED'], 'write_fail', 'Cannot write snippet');
    }
    return ok(['name' => $name, 'safe' => $safe, 'language' => $record['language'], 'tags' => $record['tags']]);
}

function action_snippet_get($req, $cfg) {
    global $E, $CONFIG;
    $name = $req['name'] ?? '';
    if ($name === '') return fail($E['INVALID_PARAM'], 'empty_name', 'name is required');
    $file = _snippet_dir($cfg) . _snippet_sanitize_name($name) . '.json';
    if (!file_exists($file)) return fail($E['NOT_FOUND'], 'not_found', "Snippet '$name' not found");
    return ok(json_decode(file_get_contents($file), true));
}

function action_snippet_list($req, $cfg) {
    global $E, $CONFIG;
    $dir = _snippet_dir($cfg);
    if (!is_dir($dir)) return ok(['snippets' => [], 'total' => 0]);
    $tag_filter = $req['tag'] ?? null;
    $lang_filter = $req['language'] ?? null;
    $keyword = $req['keyword'] ?? null;
    $items = [];
    foreach (glob($dir . '*.json') as $f) {
        $rec = json_decode(file_get_contents($f), true);
        if (!$rec) continue;
        if ($tag_filter && !in_array($tag_filter, $rec['tags'] ?? [])) continue;
        if ($lang_filter && ($rec['language'] ?? '') !== $lang_filter) continue;
        if ($keyword) {
            $hay = ($rec['name'] ?? '') . ' ' . ($rec['description'] ?? '') . ' ' . ($rec['content'] ?? '');
            if (stripos($hay, $keyword) === false) continue;
        }
        $items[] = [
            'name'        => $rec['name'] ?? '',
            'language'    => $rec['language'] ?? 'plain',
            'tags'        => $rec['tags'] ?? [],
            'description' => $rec['description'] ?? '',
            'size'        => strlen($rec['content'] ?? ''),
            'updated_at'  => $rec['updated_at'] ?? 0,
        ];
    }
    usort($items, function($a, $b){ return $b['updated_at'] - $a['updated_at']; });
    return ok(['snippets' => $items, 'total' => count($items)]);
}

function action_snippet_delete($req, $cfg) {
    global $E, $CONFIG;
    $name = $req['name'] ?? '';
    if ($name === '') return fail($E['INVALID_PARAM'], 'empty_name', 'name is required');
    $file = _snippet_dir($cfg) . _snippet_sanitize_name($name) . '.json';
    if (!file_exists($file)) return fail($E['NOT_FOUND'], 'not_found', "Snippet '$name' not found");
    @unlink($file);
    return ok(['name' => $name, 'deleted' => true]);
}
