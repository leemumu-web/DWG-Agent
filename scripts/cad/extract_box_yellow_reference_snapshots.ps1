[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ManifestPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,

    [string]$SampleId,

    [ValidateSet("complete_reference", "historical_wrong_result")]
    [string]$EvidenceField = "complete_reference"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Stop-Extract {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Code,

        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    [Console]::Error.WriteLine($Code + ": " + $Message)
    exit 1
}

function Resolve-FullPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [string]$BasePath
    )

    if ([IO.Path]::IsPathRooted($Path)) {
        return [IO.Path]::GetFullPath($Path)
    }
    if ([string]::IsNullOrWhiteSpace($BasePath)) {
        return [IO.Path]::GetFullPath((Join-Path (Get-Location).Path $Path))
    }
    return [IO.Path]::GetFullPath((Join-Path $BasePath $Path))
}

function Test-PathWithinRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Candidate,

        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    $candidateFull = [IO.Path]::GetFullPath($Candidate)
    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    if ($candidateFull.Equals($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    $prefix = $rootFull + [IO.Path]::DirectorySeparatorChar
    return $candidateFull.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
}

function Get-Sha256 {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Release-ComObject {
    param([object]$Value)

    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
}

function Convert-NumberArray {
    param([object]$Value)

    return @($Value | ForEach-Object { [double]$_ })
}

function Convert-SnapshotEntity {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Entity,

        [Parameter(Mandatory = $true)]
        [string]$SampleIdValue
    )

    $objectName = [string]$Entity.ObjectName
    $common = [ordered]@{
        object_name = $objectName
        handle = [string]$Entity.Handle
        layer = [string]$Entity.Layer
        color = [int]$Entity.Color
    }
    if ($objectName -eq "AcDbPolyline") {
        $coordinates = Convert-NumberArray -Value $Entity.Coordinates
        if ($coordinates.Count -lt 6 -or $coordinates.Count % 2 -ne 0) {
            Stop-Extract -Code "INVALID_REFERENCE_POLYLINE" -Message $SampleIdValue
        }
        $bulges = @()
        for ($vertexIndex = 0; $vertexIndex -lt ($coordinates.Count / 2); $vertexIndex++) {
            $bulges += [double]$Entity.GetBulge($vertexIndex)
        }
        return [ordered]@{
            object_name = $common.object_name
            handle = $common.handle
            layer = $common.layer
            color = $common.color
            coordinates = $coordinates
            bulges = $bulges
            closed = [bool]$Entity.Closed
            elevation = [double]$Entity.Elevation
            normal = Convert-NumberArray -Value $Entity.Normal
        }
    }
    if ($objectName -eq "AcDbCircle") {
        return [ordered]@{
            object_name = $common.object_name
            handle = $common.handle
            layer = $common.layer
            color = $common.color
            center = Convert-NumberArray -Value $Entity.Center
            radius = [double]$Entity.Radius
            normal = Convert-NumberArray -Value $Entity.Normal
        }
    }
    if ($objectName -eq "AcDbText") {
        return [ordered]@{
            object_name = $common.object_name
            handle = $common.handle
            layer = $common.layer
            color = $common.color
            text = [string]$Entity.TextString
            insertion_point = Convert-NumberArray -Value $Entity.InsertionPoint
            height = [double]$Entity.Height
            rotation = [double]$Entity.Rotation
        }
    }
    Stop-Extract -Code "UNSUPPORTED_REFERENCE_ENTITY" -Message (
        $SampleIdValue + ": " + $objectName
    )
}

function Add-ExplodedSnapshotEntities {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Entity,

        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [Collections.ArrayList]$Entities,

        [Parameter(Mandatory = $true)]
        [string]$SampleIdValue
    )

    if ([string]$Entity.ObjectName -ne "AcDbBlockReference") {
        [void]$Entities.Add((
            Convert-SnapshotEntity -Entity $Entity -SampleIdValue $SampleIdValue
        ))
        return
    }

    $children = @($Entity.Explode())
    try {
        foreach ($child in $children) {
            Add-ExplodedSnapshotEntities `
                -Entity $child `
                -Entities $Entities `
                -SampleIdValue $SampleIdValue
        }
    } finally {
        foreach ($child in $children) {
            try {
                $child.Delete()
            } catch {
                # The document is closed without saving; deletion is best-effort cleanup only.
            } finally {
                Release-ComObject -Value $child
            }
        }
    }
}

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

public static class ZWCadSnapshotPromptGuard
{
    private const uint BmClick = 0x00F5;
    private static readonly object Sync = new object();
    private static Thread worker;
    private static volatile bool stopping;
    private static int processId;
    private static int clickCount;

    private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool EnumChildWindows(
        IntPtr parent,
        EnumWindowsProc callback,
        IntPtr lParam
    );

    [DllImport("user32.dll")]
    private static extern int GetWindowTextLength(IntPtr hwnd);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(
        IntPtr hwnd,
        StringBuilder value,
        int maximum
    );

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(
        IntPtr hwnd,
        out uint ownerProcessId
    );

    [DllImport("user32.dll")]
    private static extern IntPtr SendMessage(
        IntPtr hwnd,
        uint message,
        IntPtr wParam,
        IntPtr lParam
    );

    public static int GetProcessId(IntPtr hwnd)
    {
        uint ownerProcessId;
        GetWindowThreadProcessId(hwnd, out ownerProcessId);
        return checked((int)ownerProcessId);
    }

    public static void Start(int ownerProcessId)
    {
        lock (Sync)
        {
            if (worker != null)
            {
                throw new InvalidOperationException("guard already started");
            }
            processId = ownerProcessId;
            clickCount = 0;
            stopping = false;
            worker = new Thread(Run);
            worker.IsBackground = true;
            worker.Name = "ZWCAD snapshot prompt guard";
            worker.Start();
        }
    }

    public static int Stop()
    {
        Thread current;
        lock (Sync)
        {
            current = worker;
            stopping = true;
        }
        if (current != null)
        {
            current.Join(3000);
        }
        lock (Sync)
        {
            worker = null;
            return clickCount;
        }
    }

    private static void Run()
    {
        while (!stopping)
        {
            EnumWindows(InspectTopWindow, IntPtr.Zero);
            Thread.Sleep(200);
        }
    }

    private static bool InspectTopWindow(IntPtr hwnd, IntPtr lParam)
    {
        uint ownerProcessId;
        GetWindowThreadProcessId(hwnd, out ownerProcessId);
        if (ownerProcessId != processId)
        {
            return true;
        }
        if (ReadText(hwnd) != "\u6388\u6743\u63d0\u793a")
        {
            return true;
        }
        EnumChildWindows(hwnd, InspectPromptChild, IntPtr.Zero);
        return true;
    }

    private static bool InspectPromptChild(IntPtr hwnd, IntPtr lParam)
    {
        if (ReadText(hwnd) != "\u786e\u5b9a")
        {
            return true;
        }
        SendMessage(hwnd, BmClick, IntPtr.Zero, IntPtr.Zero);
        Interlocked.Increment(ref clickCount);
        return false;
    }

    private static string ReadText(IntPtr hwnd)
    {
        int length = GetWindowTextLength(hwnd);
        StringBuilder value = new StringBuilder(length + 1);
        GetWindowText(hwnd, value, value.Capacity);
        return value.ToString();
    }
}
"@

$manifestFull = Resolve-FullPath -Path $ManifestPath
if (-not (Test-Path -LiteralPath $manifestFull -PathType Leaf)) {
    Stop-Extract -Code "MANIFEST_NOT_FOUND" -Message $manifestFull
}
try {
    $manifest = Get-Content -LiteralPath $manifestFull -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    Stop-Extract -Code "MANIFEST_INVALID_JSON" -Message $_.Exception.Message
}
if ($manifest.schema -ne "BOX-EXTERNAL-ACCEPTANCE-CONTRACTS-1.0") {
    Stop-Extract -Code "MANIFEST_SCHEMA_INVALID" -Message ([string]$manifest.schema)
}

$corpusRoot = Resolve-FullPath -Path ([string]$manifest.corpus_root)
if (-not (Test-Path -LiteralPath $corpusRoot -PathType Container)) {
    Stop-Extract -Code "CORPUS_ROOT_NOT_FOUND" -Message $corpusRoot
}
$outputFull = Resolve-FullPath -Path $OutputRoot
if (Test-PathWithinRoot -Candidate $outputFull -Root $corpusRoot) {
    Stop-Extract -Code "OUTPUT_ROOT_INSIDE_CORPUS" -Message $outputFull
}

$historicalMode = $EvidenceField -eq "historical_wrong_result"
$snapshotSchema = if ($historicalMode) {
    "BOX-HISTORICAL-WRONG-RESULT-SNAPSHOT-1.0"
} else {
    "BOX-YELLOW-REFERENCE-SNAPSHOT-1.0"
}
$snapshotSuffix = if ($historicalMode) {
    "_historical-wrong-result.json"
} else {
    "_correct-reference.json"
}
$reportSchema = if ($historicalMode) {
    "BOX-HISTORICAL-WRONG-RESULT-SNAPSHOT-REPORT-1.0"
} else {
    "BOX-YELLOW-REFERENCE-SNAPSHOT-REPORT-1.0"
}
$reportName = if ($historicalMode) {
    "historical-wrong-result-snapshot-report.json"
} else {
    "yellow-reference-snapshot-report.json"
}

$selected = @(
    $manifest.samples | Where-Object {
        $property = $_.PSObject.Properties[$EvidenceField]
        $null -ne $property -and $null -ne $property.Value -and (
            [string]::IsNullOrWhiteSpace($SampleId) -or $_.sample_id -eq $SampleId
        )
    }
)
if (-not [string]::IsNullOrWhiteSpace($SampleId) -and $selected.Count -ne 1) {
    Stop-Extract -Code "SAMPLE_NOT_FOUND" -Message $SampleId
}
if ($selected.Count -eq 0) {
    if ($historicalMode) {
        Stop-Extract -Code "NO_HISTORICAL_RESULTS" -Message "manifest has no historical wrong results"
    }
    Stop-Extract -Code "NO_COMPLETE_REFERENCES" -Message "manifest has no complete references"
}

$validated = @()
foreach ($sample in $selected) {
    $sampleIdValue = [string]$sample.sample_id
    if ([string]::IsNullOrWhiteSpace($sampleIdValue)) {
        Stop-Extract -Code "SAMPLE_ID_INVALID" -Message "sample id is empty"
    }
    if ($sampleIdValue.IndexOfAny([IO.Path]::GetInvalidFileNameChars()) -ge 0) {
        Stop-Extract -Code "SAMPLE_ID_INVALID" -Message $sampleIdValue
    }
    $reference = $sample.PSObject.Properties[$EvidenceField].Value
    $relativePath = [string]$reference.relative_path
    $sourcePath = Resolve-FullPath -Path $relativePath -BasePath $corpusRoot
    if (-not (Test-PathWithinRoot -Candidate $sourcePath -Root $corpusRoot)) {
        Stop-Extract -Code "SOURCE_PATH_OUTSIDE_CORPUS" -Message $sourcePath
    }
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        Stop-Extract -Code "SOURCE_NOT_FOUND" -Message $sourcePath
    }
    if ([IO.Path]::GetExtension($sourcePath) -ine ".dwg") {
        Stop-Extract -Code "SOURCE_NOT_DWG" -Message $sourcePath
    }
    $expectedHash = ([string]$reference.sha256).ToLowerInvariant()
    $actualHash = Get-Sha256 -Path $sourcePath
    if ($actualHash -ne $expectedHash) {
        Stop-Extract -Code "SOURCE_SHA256_MISMATCH" -Message $sampleIdValue
    }
    $validated += [pscustomobject]@{
        SampleId = $sampleIdValue
        RelativePath = $relativePath
        SourcePath = $sourcePath
        SourceHash = $actualHash
        TargetPath = Join-Path $outputFull ($sampleIdValue + $snapshotSuffix)
    }
}

New-Item -ItemType Directory -Path $outputFull -Force | Out-Null
$application = $null
$documents = $null
$activeDocumentFullName = $null
$guardStarted = $false
$licensePromptClicks = 0
$records = @()
try {
    try {
        $application = [Runtime.InteropServices.Marshal]::GetActiveObject(
            "ZWCAD.Application.2026"
        )
    } catch {
        Stop-Extract -Code "ZWCAD_NOT_RUNNING" -Message $_.Exception.Message
    }
    $applicationHwnd = [IntPtr][Int64]$application.HWND
    $applicationProcessId = [ZWCadSnapshotPromptGuard]::GetProcessId($applicationHwnd)
    [ZWCadSnapshotPromptGuard]::Start($applicationProcessId)
    $guardStarted = $true
    $documents = $application.Documents
    try {
        if ($null -ne $application.ActiveDocument) {
            $activeDocumentFullName = [string]$application.ActiveDocument.FullName
        }
    } catch {
        $activeDocumentFullName = $null
    }

    foreach ($item in $validated) {
        $document = $null
        $modelSpace = $null
        $started = Get-Date
        $entities = [Collections.ArrayList]::new()
        try {
            $document = $documents.Open($item.SourcePath, $true)
            $modelSpace = $document.ModelSpace
            $initialModelSpaceCount = [int]$modelSpace.Count
            for ($index = 0; $index -lt $initialModelSpaceCount; $index++) {
                $entity = $null
                try {
                    $entity = $modelSpace.Item($index)
                    $objectName = [string]$entity.ObjectName
                    if ($historicalMode) {
                        if ($objectName -eq "AcDbBlockReference") {
                            Add-ExplodedSnapshotEntities `
                                -Entity $entity `
                                -Entities $entities `
                                -SampleIdValue $item.SampleId
                        } elseif (
                            $objectName -eq "AcDbText" -and
                            [string]$entity.Layer -in @("PART_LABEL", "ERROR_NOTE")
                        ) {
                            # Human notes live outside the program-result blocks.
                        } else {
                            Stop-Extract -Code "UNSUPPORTED_HISTORICAL_TOP_LEVEL_ENTITY" -Message (
                                $item.SampleId + ": " + $objectName + "@" + [string]$entity.Layer
                            )
                        }
                    } else {
                        [void]$entities.Add((
                            Convert-SnapshotEntity `
                                -Entity $entity `
                                -SampleIdValue $item.SampleId
                        ))
                    }
                } finally {
                    Release-ComObject -Value $entity
                }
            }
        } catch {
            if ($_.Exception.Message -match "^[A-Z_]+:") {
                throw
            }
            Stop-Extract -Code "ZWCAD_REFERENCE_READ_FAILED" -Message (
                $item.SampleId + ": " + $_.Exception.Message
            )
        } finally {
            Release-ComObject -Value $modelSpace
            if ($null -ne $document) {
                try {
                    $document.Close($false)
                } finally {
                    Release-ComObject -Value $document
                }
            }
        }

        $sourceHashAfter = Get-Sha256 -Path $item.SourcePath
        if ($sourceHashAfter -ne $item.SourceHash) {
            Stop-Extract -Code "SOURCE_DRIFT_AFTER_READ" -Message $item.SampleId
        }
        $snapshot = [ordered]@{
            schema = $snapshotSchema
            sample_id = $item.SampleId
            source = [ordered]@{
                relative_path = $item.RelativePath
                sha256_before = $item.SourceHash
                sha256_after = $sourceHashAfter
                unchanged = $true
            }
            zwcad_progid = "ZWCAD.Application.2026"
            model_space_count = $entities.Count
            entities = $entities
        }
        $snapshot | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $item.TargetPath -Encoding UTF8
        $records += [ordered]@{
            sample_id = $item.SampleId
            source_relative_path = $item.RelativePath
            source_sha256_before = $item.SourceHash
            source_sha256_after = $sourceHashAfter
            source_unchanged = $true
            snapshot_path = [IO.Path]::GetFullPath($item.TargetPath)
            snapshot_sha256 = Get-Sha256 -Path $item.TargetPath
            entity_count = $entities.Count
            duration_seconds = ((Get-Date) - $started).TotalSeconds
            status = "extracted"
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($activeDocumentFullName)) {
        for ($documentIndex = 0; $documentIndex -lt [int]$documents.Count; $documentIndex++) {
            $openDocument = $null
            try {
                $openDocument = $documents.Item($documentIndex)
                if ([string]$openDocument.FullName -ieq $activeDocumentFullName) {
                    $openDocument.Activate()
                    break
                }
            } finally {
                Release-ComObject -Value $openDocument
            }
        }
    }
} finally {
    if ($guardStarted) {
        $licensePromptClicks = [ZWCadSnapshotPromptGuard]::Stop()
    }
    Release-ComObject -Value $documents
    Release-ComObject -Value $application
}

$report = [ordered]@{
    schema = $reportSchema
    evidence_field = $EvidenceField
    manifest_path = $manifestFull
    corpus_root = $corpusRoot
    output_root = $outputFull
    extracted = $records.Count
    failed = 0
    source_drift = 0
    license_prompt_clicks = $licensePromptClicks
    records = $records
}
$reportPath = Join-Path $outputFull $reportName
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding UTF8
$report | ConvertTo-Json -Depth 4 -Compress
