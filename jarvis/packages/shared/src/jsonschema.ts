/**
 * A deliberately small JSON Schema validator.
 *
 * Tool inputs are declared once, as JSON Schema, and that same object is what
 * gets handed to the model for function calling. Using a validation library
 * with its own DSL would mean maintaining two descriptions of the same shape,
 * so we validate the schema we already publish instead.
 *
 * Supported: type, properties, required, additionalProperties (boolean),
 * items, enum, default, minimum/maximum, minLength/maxLength.
 * Anything else in the schema is ignored rather than rejected.
 */
import type { JsonSchema } from './types.ts';

export interface ValidationOk {
  ok: true;
  /** Input with `default`s filled in and unknown keys stripped. */
  value: Record<string, unknown>;
}
export interface ValidationErr {
  ok: false;
  errors: string[];
}
export type ValidationResult = ValidationOk | ValidationErr;

function typeOf(value: unknown): string {
  if (value === null) return 'null';
  if (Array.isArray(value)) return 'array';
  return typeof value;
}

function matchesType(value: unknown, type: NonNullable<JsonSchema['type']>): boolean {
  switch (type) {
    case 'object':
      return typeOf(value) === 'object';
    case 'array':
      return Array.isArray(value);
    case 'string':
      return typeof value === 'string';
    case 'number':
      return typeof value === 'number' && Number.isFinite(value);
    case 'integer':
      return typeof value === 'number' && Number.isInteger(value);
    case 'boolean':
      return typeof value === 'boolean';
    case 'null':
      return value === null;
  }
}

function validateValue(value: unknown, schema: JsonSchema, path: string, errors: string[]): unknown {
  if (schema.type && !matchesType(value, schema.type)) {
    errors.push(`${path}: expected ${schema.type}, received ${typeOf(value)}`);
    return value;
  }

  if (schema.enum && !schema.enum.some((option) => option === value)) {
    errors.push(`${path}: must be one of ${schema.enum.map((o) => JSON.stringify(o)).join(', ')}`);
    return value;
  }

  if (typeof value === 'number') {
    if (schema.minimum !== undefined && value < schema.minimum) {
      errors.push(`${path}: must be >= ${schema.minimum}`);
    }
    if (schema.maximum !== undefined && value > schema.maximum) {
      errors.push(`${path}: must be <= ${schema.maximum}`);
    }
  }

  if (typeof value === 'string') {
    if (schema.minLength !== undefined && value.length < schema.minLength) {
      errors.push(`${path}: must be at least ${schema.minLength} characters`);
    }
    if (schema.maxLength !== undefined && value.length > schema.maxLength) {
      errors.push(`${path}: must be at most ${schema.maxLength} characters`);
    }
  }

  if (Array.isArray(value) && schema.items) {
    return value.map((item, i) => validateValue(item, schema.items!, `${path}[${i}]`, errors));
  }

  if (schema.type === 'object' || schema.properties) {
    if (typeOf(value) !== 'object') return value;
    return validateObject(value as Record<string, unknown>, schema, path, errors);
  }

  return value;
}

function validateObject(
  input: Record<string, unknown>,
  schema: JsonSchema,
  path: string,
  errors: string[],
): Record<string, unknown> {
  const properties = schema.properties ?? {};
  const required = schema.required ?? [];
  const out: Record<string, unknown> = {};

  for (const key of required) {
    if (input[key] === undefined || input[key] === null) {
      errors.push(`${path ? `${path}.` : ''}${key}: required`);
    }
  }

  for (const [key, propSchema] of Object.entries(properties)) {
    const childPath = path ? `${path}.${key}` : key;
    const raw = input[key];
    if (raw === undefined) {
      if (propSchema.default !== undefined) out[key] = propSchema.default;
      continue;
    }
    out[key] = validateValue(raw, propSchema, childPath, errors);
  }

  if (schema.additionalProperties !== false) {
    for (const [key, raw] of Object.entries(input)) {
      if (!(key in properties)) out[key] = raw;
    }
  } else {
    for (const key of Object.keys(input)) {
      if (!(key in properties)) {
        errors.push(`${path ? `${path}.` : ''}${key}: unexpected property`);
      }
    }
  }

  return out;
}

/** Validate tool arguments against the tool's declared input schema. */
export function validateInput(input: unknown, schema: JsonSchema): ValidationResult {
  const errors: string[] = [];
  const source: Record<string, unknown> =
    input && typeOf(input) === 'object' ? (input as Record<string, unknown>) : {};

  if (input !== undefined && input !== null && typeOf(input) !== 'object') {
    return { ok: false, errors: [`arguments: expected object, received ${typeOf(input)}`] };
  }

  const value = validateObject(source, schema, '', errors);
  return errors.length > 0 ? { ok: false, errors } : { ok: true, value };
}
