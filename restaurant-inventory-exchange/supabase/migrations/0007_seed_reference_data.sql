-- Starting locations and catalog. Nothing in the application is coupled to
-- these rows; they are simply a useful first day. Admins add, rename and
-- deactivate locations and items from the app.

insert into public.locations (name)
select v.name
from (values ('Hibachio 1'), ('Hibachio 2'), ('Hibachio 3'), ('287 Taco Shop')) as v(name)
where not exists (
  select 1 from public.locations l where lower(l.name) = lower(v.name)
);

insert into public.inventory_items (name, category, unit)
select v.name, v.category, v.unit
from (values
  ('32 oz Cups',         'Cups',       'sleeve'),
  ('32 oz Lids',         'Lids',       'sleeve'),
  ('16 oz Cups',         'Cups',       'sleeve'),
  ('16 oz Lids',         'Lids',       'sleeve'),
  ('Napkins',            'Paper',      'case'),
  ('Tortilla Chips',     'Food',       'case'),
  ('Gloves',             'Supplies',   'box'),
  ('Foil',               'Supplies',   'roll'),
  ('Takeout Containers', 'Packaging',  'case'),
  ('Straws',             'Paper',      'box'),
  ('To-Go Bags',         'Packaging',  'bundle')
) as v(name, category, unit)
where not exists (
  select 1 from public.inventory_items i
  where lower(i.name) = lower(v.name) and i.location_id is null
);
