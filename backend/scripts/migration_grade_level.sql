-- Grade level migration
-- Run this in Supabase SQL editor (Dashboard → SQL Editor)

-- Add grade_level column to user_profiles
alter table user_profiles
  add column if not exists grade_level text;
