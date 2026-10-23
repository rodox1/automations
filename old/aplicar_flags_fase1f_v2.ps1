$ErrorActionPreference = "Stop"

$projectRoot = Get-Location
$target = Join-Path $projectRoot "app\api\provisioning.py"
$backup = "$target.bak_phase1f"

if (-not (Test-Path $target)) {
    throw "Arquivo não encontrado: $target"
}

if (-not (Test-Path $backup)) {
    Copy-Item $target $backup
    Write-Host "Backup criado: $backup"
}
else {
    Write-Host "Backup existente preservado: $backup"
}

$text = [System.IO.File]::ReadAllText($target, [System.Text.Encoding]::UTF8)
$original = $text

# ------------------------------------------------------------
# 1) Falha controlada da compensação
# ------------------------------------------------------------
$compMarker = 'try:`r`n            _delete_target_user('

if ($text.Contains('AVAYA_PROVISIONING_TEST_FORCE_COMPENSATION_FAIL')) {
    Write-Host "Flag de compensação já está presente."
}
else {
    $needle = "        try:`r`n            _delete_target_user("
    $replacement = @"
        try:
            import os
            if os.getenv("AVAYA_PROVISIONING_TEST_FORCE_COMPENSATION_FAIL") == "1":
                logger.warning(
                    "[PROVISIONING TEST] Falha controlada da compensação | target_ip=%s | guid=%s",
                    target_ip,
                    result.guid,
                )
                raise RuntimeError(
                    "TEST_FORCE_COMPENSATION_FAIL: falha controlada da compensação."
                )

            _delete_target_user(
"@

    if (-not $text.Contains($needle)) {
        # tentar variante LF
        $needle = "        try:`n            _delete_target_user("
        $replacement = $replacement -replace "`r`n","`n"
    }

    if (-not $text.Contains($needle)) {
        throw "Não foi possível localizar o início estável do bloco de compensação."
    }

    $text = $text.Replace($needle, $replacement)
    Write-Host "Flag AVAYA_PROVISIONING_TEST_FORCE_COMPENSATION_FAIL adicionada."
}

# ------------------------------------------------------------
# 2) Falha controlada da persistência do cache
# ------------------------------------------------------------
if ($text.Contains('AVAYA_PROVISIONING_TEST_FORCE_CACHE_FAIL')) {
    Write-Host "Flag de cache já está presente."
}
else {
    $needle = @"
        _cache_verified_user(
            db,
            verified_user,
            target_ip,
        )
"@

    $replacement = @"
        import os
        if os.getenv("AVAYA_PROVISIONING_TEST_FORCE_CACHE_FAIL") == "1":
            logger.warning(
                "[PROVISIONING TEST] Falha controlada do cache | target_ip=%s | guid=%s",
                target_ip,
                result.guid,
            )
            raise RuntimeError(
                "TEST_FORCE_CACHE_FAIL: falha controlada da persistência do cache."
            )

        _cache_verified_user(
            db,
            verified_user,
            target_ip,
        )
"@

    if (-not $text.Contains($needle)) {
        $needle = $needle -replace "`r`n","`n"
        $replacement = $replacement -replace "`r`n","`n"
    }

    if (-not $text.Contains($needle)) {
        throw "Não foi possível localizar o início estável do bloco de cache."
    }

    $text = $text.Replace($needle, $replacement)
    Write-Host "Flag AVAYA_PROVISIONING_TEST_FORCE_CACHE_FAIL adicionada."
}

if ($text -eq $original) {
    Write-Host "Nenhuma alteração necessária."
}
else {
    [System.IO.File]::WriteAllText(
        $target,
        $text,
        (New-Object System.Text.UTF8Encoding($false))
    )
    Write-Host "Arquivo atualizado: $target"
}

# Validação de sintaxe
python -m py_compile $target

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERRO: py_compile falhou. Restaurando backup..."
    Copy-Item $backup $target -Force
    python -m py_compile $target
    throw "Alteração revertida porque a validação de sintaxe falhou."
}

Write-Host ""
Write-Host "FASE 1F - preparação concluída com sucesso."
Write-Host "Flags disponíveis:"
Write-Host "  AVAYA_PROVISIONING_TEST_FORCE_COMPENSATION_FAIL=1"
Write-Host "  AVAYA_PROVISIONING_TEST_FORCE_CACHE_FAIL=1"
Write-Host ""
Write-Host "Nenhuma flag foi ativada neste momento."
