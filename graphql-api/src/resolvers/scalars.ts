import { GraphQLScalarType, Kind } from "graphql";

/**
 * DateTime: ISO 8601 instant, always UTC on the wire (see the design doc's
 * Scalars section). Accepts a JS Date (from Mongo BSON datetimes) or an ISO
 * string on the way out; accepts an ISO string as an input literal/variable.
 */
export const DateTimeScalar = new GraphQLScalarType({
  name: "DateTime",
  description: "ISO 8601 instant, always serialized in UTC.",
  serialize(value: unknown): string {
    const date = value instanceof Date ? value : new Date(String(value));
    if (Number.isNaN(date.getTime())) {
      throw new TypeError(`DateTime cannot serialize invalid date value`);
    }
    return date.toISOString();
  },
  parseValue(value: unknown): Date {
    const date = new Date(String(value));
    if (Number.isNaN(date.getTime())) {
      throw new TypeError(`DateTime cannot parse invalid value`);
    }
    return date;
  },
  parseLiteral(ast): Date {
    if (ast.kind !== Kind.STRING) {
      throw new TypeError("DateTime literal must be a string");
    }
    const date = new Date(ast.value);
    if (Number.isNaN(date.getTime())) {
      throw new TypeError(`DateTime cannot parse invalid literal`);
    }
    return date;
  },
});

/** Date: YYYY-MM-DD local calendar date, stored and passed through as a plain string. */
export const DateScalar = new GraphQLScalarType({
  name: "Date",
  description: "A local calendar date in YYYY-MM-DD format.",
  serialize(value: unknown): string {
    if (typeof value !== "string") {
      throw new TypeError("Date must serialize from a YYYY-MM-DD string");
    }
    return value;
  },
  parseValue(value: unknown): string {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
      throw new TypeError("Date must be a YYYY-MM-DD string");
    }
    return value;
  },
  parseLiteral(ast): string {
    if (ast.kind !== Kind.STRING || !/^\d{4}-\d{2}-\d{2}$/.test(ast.value)) {
      throw new TypeError("Date literal must be a YYYY-MM-DD string");
    }
    return ast.value;
  },
});

/** JSON: opaque escape hatch for breakdown30Day / error / result diagnostic blobs. */
export const JSONScalar = new GraphQLScalarType({
  name: "JSON",
  description: "Opaque JSON value.",
  serialize(value: unknown) {
    return value;
  },
  parseValue(value: unknown) {
    return value;
  },
  parseLiteral(ast) {
    return parseLiteralToJson(ast);
  },
});

function parseLiteralToJson(ast: import("graphql").ValueNode): unknown {
  switch (ast.kind) {
    case Kind.STRING:
    case Kind.BOOLEAN:
      return ast.value;
    case Kind.INT:
    case Kind.FLOAT:
      return Number(ast.value);
    case Kind.OBJECT: {
      const result: Record<string, unknown> = {};
      for (const field of ast.fields) {
        result[field.name.value] = parseLiteralToJson(field.value);
      }
      return result;
    }
    case Kind.LIST:
      return ast.values.map(parseLiteralToJson);
    case Kind.NULL:
      return null;
    default:
      return null;
  }
}
