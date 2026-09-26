# frozen_string_literal: true

require "rake/testtask"

Rake::TestTask.new do |task|
  task.libs << "lib"
  task.pattern = "tests/**/*_test.rb"
end

task default: :test

