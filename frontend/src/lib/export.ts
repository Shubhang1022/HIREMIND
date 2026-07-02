/**
 * Client-side export helpers for demo and recruiter workflows.
 */

export function downloadBlob(content: string, filename: string, mimeType: string) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function exportToCsv<T extends Record<string, unknown>>(
  rows: T[],
  filename: string,
  columns?: { key: keyof T; header: string }[]
) {
  if (rows.length === 0) {
    throw new Error('No data to export');
  }

  const cols =
    columns ??
    (Object.keys(rows[0]) as (keyof T)[]).map((key) => ({
      key,
      header: String(key),
    }));

  const escape = (value: unknown) => {
    const text = String(value ?? '');
    if (text.includes(',') || text.includes('"') || text.includes('\n')) {
      return `"${text.replace(/"/g, '""')}"`;
    }
    return text;
  };

  const header = cols.map((c) => escape(c.header)).join(',');
  const body = rows
    .map((row) => cols.map((c) => escape(row[c.key])).join(','))
    .join('\n');

  downloadBlob(`${header}\n${body}`, filename, 'text/csv;charset=utf-8');
}

export function exportToJson(data: unknown, filename: string) {
  downloadBlob(JSON.stringify(data, null, 2), filename, 'application/json');
}
