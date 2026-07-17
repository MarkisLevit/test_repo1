SELECT employee_id, first_name, last_name, salary
FROM parks_and_recreation.employee_salary
WHERE salary >= 29000
;

SELECT *
FROM parks_departments;

SELECT salary, 
last_name,
employee_id,
salary * 2
FROM parks_and_recreation.employee_salary;

SELECT *
FROM employee_salary
WHERE first_name LIKE 'a%' AND salary > 20000;

SELECT *
FROM employee_salary
WHERE NOT (salary <= 85000 AND salary >= 50000);

SELECT *
FROM parks_and_recreation.employee_demographics
WHERE first_name LIKE '_o' or birth_date LIKE '1981%';

-- what i have learned: SELECT for columns, FROM to chouse table, WHERE for rows, OR AND NOT, LIKE for WHERE( % continue anything and _ for specific amount of characters)

SELECT *
FROM parks_and_recreation.employee_demographics
WHERE (age > 20 AND age < 35) AND (first_name LIKE 'An%');